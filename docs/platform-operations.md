# 平台部署与运维

本文对应 `app.platform_app:app` / `compose.platform.yaml`。默认用于本地模拟验收；企业上线仍需真实接入、安全审查、容量和恢复验证。采用一家企业专属部署、内部组织与资产授权，不含跨企业 SaaS 运营。

## 服务和启动

运行根目录 `start-platform.ps1`；已有 `.env.platform` 时也可执行：

```powershell
docker compose --env-file .env.platform -f compose.platform.yaml up -d --build
docker compose --env-file .env.platform -f compose.platform.yaml ps
docker compose --env-file .env.platform -f compose.platform.yaml logs --tail 80 api worker dispatcher
```

服务为 MySQL 8.4、Redis 7、一次性 Alembic migrate、FastAPI API、Celery Worker、Outbox dispatcher。API 默认仅绑定 `127.0.0.1:18081`；DB/Redis 不暴露主机端口。API、Worker、dispatcher 使用相同镜像和配置，共享证据卷。

`/healthz` 检查进程；`/readyz` 检查 DB、Redis、Worker 心跳，不代表模型/网关/SSO/S3 已联通。`stop` / `start` 可停启服务，`down` 保留卷；不要用 `down -v` 删除数据。应用容器以非 root 运行；企业还需镜像扫描、补丁和 digest 固定。

## 配置和凭据

`.env.platform.example` 是模板。真实环境文件、身份映射、备份及运行数据不得提交。DB/Redis 密码需要 URL 安全字符；启动脚本生成随机十六进制值，已有配置不覆盖。修改文件中的密码并不等于完成既有数据库用户密码轮换。

| 配置 | 作用 |
| --- | --- |
| `SECOPS_MODE` | development / production |
| `SECOPS_MODEL_MODE` | simulated / live |
| `SECOPS_LLM_BASE_URL`, `SECOPS_LLM_MODEL`, `SECOPS_LLM_API_KEY` | 模型 API；base URL 通常含 `/v1`，代码追加 `/chat/completions` |
| `SECOPS_CONNECTOR_MODE` | simulated / gateway |
| `SECOPS_CONNECTOR_URL`, `SECOPS_CONNECTOR_TOKEN`, `SECOPS_CONNECTOR_VERSION` | 固定企业网关和版本 |
| `SECOPS_PUBLIC_URL`, `SECOPS_ALLOWED_HOSTS` | 公开地址及 Host 白名单；TLS 由企业代理提供 |
| `SECOPS_OIDC_ISSUER`, `SECOPS_OIDC_CLIENT_ID`, `SECOPS_OIDC_CLIENT_SECRET` | 企业 OIDC，回调为公开地址 + `/auth/callback` |
| `SECOPS_IDENTITIES_FILE` | 服务端 org / roles / assets 映射 |
| `SECOPS_ATTACHMENT_BUCKET`, `SECOPS_ATTACHMENT_ENDPOINT` | 留空使用共享卷；有 bucket 时使用 S3 |
| `SECOPS_MAX_ROUNDS`, `SECOPS_MAX_TOOLS`, `SECOPS_MAX_SECONDS`, `SECOPS_MAX_TOKENS` | 默认预算 12 / 24 / 300 / 64000 |
| `SECOPS_TRACE_CONSOLE=1` | 输出 OpenTelemetry JSON spans，不含提示词或证据正文 |

预算上限 48 轮、96 工具、300 秒、100 万 Token。人工等待不计主动运行时间；人工继续开启新预算段，保留历史。修改默认值不会重写在途快照。调用前做保守字节数预算预估、调用后核对 usage；尚无 tokenizer 校准及供应商账单对账。超时请求可能已经消耗 Token，因此不能视作严格费用上限。

秘密配置支持 `_FILE`，如 `SECOPS_LLM_API_KEY_FILE`、`SECOPS_DATABASE_URL_FILE`，需要将文件挂载到各服务。模板默认 env 注入，不是密钥保险库。生产应使用企业 secret manager、限制文件权限和网络出口。不要输出展开后的 `docker compose config`、环境文件或连接串。

S3 使用 boto3 标准身份链。企业应启用对象版本、访问审计、生命周期及适用加密。共享卷 hash 校验不是不可篡改证据保全。导出先校验资产权限，再检查归档 hash。

## 身份和生产模式

将 `config/identities.example.json` 复制为实际映射，以可信 OIDC `sub` 为键：

```json
{"employee-sub":{"enabled":true,"org":"enterprise-a","roles":["analyst"],"assets":["asset-id-001"]}}
```

资产使用企业内唯一 ID。分析员调查/复核/申请；负责人可审批；执行员执行/核对；审计员和管理员读取审计/指标。管理员不自动获得处置权限。仅申请人或负责人能撤回尚未执行申请。自批被禁止；执行时再次核对申请人/审批人权限、参数、连接器、审批有效期和运行状态。

映射每次访问及 Worker 每步读取，撤销后现有会话失去业务权限。当前通过文件管理，没有可视化配置发布工作流。OIDC 支持 RS256、Code + PKCE、state/nonce/浏览器绑定、签名/有效期校验和服务端会话；discovery 的端点必须和 issuer 同源且 HTTPS。跨域 IdP 需要评估后扩展。

production 要求 MySQL、Celery、真实模型、gateway、SSO/身份映射和 HTTPS，禁止通配 Host、演示身份、模拟写操作；不隐式建表。生产入口需 TLS、限流和网络控制；写请求要求与公开入口同源的 Origin。当前告警 API 使用用户会话，尚无专用 SIEM 机器身份 webhook。

## 模型及企业网关合约

模型需支持 `tools`、`tool_choice=required`、`max_tokens`。观察匹配 `tool_call_id` 返回；只允许六种只读工具、报告提交、人工接管。LLM 不直接封禁或冻结；人工依据调查结果申请处置。模型异常转人工，不静默切换模拟策略。

真实告警 `POST /api/platform/runs`，alert 与 scenario 不能同时提供：

```json
{"alert":{"name":"异常导出","category":"web","asset":"asset-id-001","ip":"192.0.2.10","path":"/exports","account":"operator-1","event_time":"2026-10-17T01:00:00Z"}}
```

event_time 必须有时区，未传时使用接入时间。查询默认前后 30 分钟，单窗最多 24 小时；资产、账号、来源 IP、路径由服务器绑定。limit/cursor 可控制分页；输出最大 64 KB。对不完整页采用保守校验，最后一页完整不能替代全窗口完整覆盖。

网关头部包含 `Authorization: Bearer ...` 和 `X-Connector-Version`。部署方需实现具体 SIEM/WAF/IAM 映射，代码没有厂商专用连接器。

`POST /queries/{tool}` 接收 asset/account/source_ip/path/start/end/limit/cursor：

```json
{"source":"siem/query-id","complete":true,"summary":"查询摘要","data":{"suspicious_pattern":true,"final_action":"block"}}
```

| 工具 | data 最低字段 |
| --- | --- |
| asset_context | asset, owner |
| security_logs | suspicious_pattern, final_action（block/observe/allow/pass） |
| authorization | authorized, scope_matches, within_window |
| business_audit | sensitive_export |
| auth_logs | unexpected_login, mfa_passed, owner_confirmed_unexpected |
| playbook | document_id, version, steps 字符串数组 |

布尔值必须是真实 JSON true/false。附加字段可放记录、覆盖率、事件时间和来源；无权限/未知事实不能填阴性值并宣称 complete。敏感键、邮件及常见手机号有脱敏规则，自定义字段仍需企业验证。

`POST /actions` 接收 operation_id 和 action（kind、target、asset、duration_minutes），带稳定 `Idempotency-Key`。只支持 block_ip / freeze_account，1–60 分钟。网关必须持久保证同 ID 不重复施加副作用、措施到期解除；平台并未代替设备实现解除逻辑。

`GET /actions/{operation_id}` 返回 applied / failed / expired 和外部引用。超时进入 execution_unknown，只核对而不重放或撤回；执行进程中断残留 executing 同样查外部状态。回查仍生效并由人工填写依据后，动作确认与事件关闭在一个事务中完成。补偿和提前解除依赖企业网关/设备流程，尚无通用解除 API。

## 迁移、发布、备份和恢复

migrate 执行 `alembic upgrade head`：0001 建表，0002 修正 MySQL 时间戳双精度，避免租约/审批时间被 FLOAT 舍入。先备份再迁移，不支持破坏性自动 downgrade。旧 SQLite 未实现导入器，继续通过旧入口访问，未删除或改写。

运行绑定模型/网关/归档配置指纹；改变配置后恢复转人工，不能混用模拟和真实证据。发布前停止新调查、等待安全边界、查清 executing/unknown。备份 MySQL 一致性快照、同一时间边界的证据存储、配置、镜像版本。更新并迁移后验证 readiness、调查/审批/回查及越权拒绝；回退镜像必须兼容当前 Schema，回退应用不会撤销外部动作。

开发环境可运行 `scripts/verify_backup_restore.py`：逻辑备份后恢复到独立探测库，检查表记录数，仅清理自己创建的探测库，保留 `.backups/` SQL 文件。脚本禁止 production。企业备份应加密、限制访问，并在独立环境验证数据、证据 hash、执行台账；运行中复制数据库物理文件不是恢复方案。

Worker 获取 120 秒租约，每 checkpoint 续租；旧 Worker 提交由 fencing token 拒绝。dispatcher 重投过期任务，Outbox/Celery 都是至少一次。只读调用若成功但结果未落库可能重查；写动作不在循环恢复时自动重放。

模拟集成验证：

```powershell
docker compose --env-file .env.platform -f compose.platform.yaml cp scripts/verify_platform.py api:/tmp/verify_platform.py
docker compose --env-file .env.platform -f compose.platform.yaml exec -T -e PYTHONPATH=/app api python /tmp/verify_platform.py
```

崩溃恢复：复制 verify_recovery.py 至 API `/tmp/verify_recovery.py`，停止本项目 dispatcher，执行 `--prepare`；无论结果如何均启动 dispatcher，再执行 `--finish`。只终止探测子进程及加速本次测试租约，不等同于所有故障点验证。

## 观测与生产准入

`/metrics` 仅限已登录 admin/auditor，按组织/资产过滤；提供持久运行计数、未发布 Outbox、最旧活动时长。生产抓取需企业授权代理或专门机器身份方案。OpenTelemetry 当前输出 console spans，可由日志平台采集；未配置 OTLP Collector/Grafana 或完整请求链路埋点。

建议监控 readiness、Outbox/审批积压、活动超时、needs_attention、execution_unknown、DB/Redis 容量。业务审计独立于诊断日志；保留期限与清理任务仍需企业策略。

上线前补充真实模型评测、实际 IdP/撤权、至少一个真实只读源及授权写目标、未知执行/到期解除、独立备份恢复、权限与负载故障测试、浏览器功能和可访问性、依赖扫描、告警及值守。未承诺 P95、并发、RPO/RTO 或生产 SLA。
