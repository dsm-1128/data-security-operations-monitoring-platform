# 平台交付与验证记录

日期：2026-10-17。范围：企业安全运营 Agent Loop 平台基础版本；不代表生产验收通过。

## 已实现

- 模型 tool calling → 工具 observation → 下一轮决策的 LangGraph 循环；预算、工具白名单、参数范围、引用/完整性校验、无进展和错误转人工。
- MySQL 持久化、Alembic 迁移、事务 Outbox、Redis/Celery 独立 Worker、租约/fencing、决策和观察快照恢复。
- OIDC Code + PKCE、服务端会话、组织/资产授权、复核、审批绑定、执行台账、未知结果查询、未执行申请撤回、原子回查关闭。
- 独立安全工作台、实时事件/SSE、轨迹/证据、归档 hash 校验与下载、授权指标、控制台 OTel spans。
- Docker Compose、启动脚本、依赖锁定、CI 配置、网关协议与运维文档。

## 实测结果

| 验证 | 结果和边界 |
| --- | --- |
| 全量 pytest | 71 passed，1 skipped；含原 Demo 回归、动态循环、授权、审批、API、OIDC/模型 mock 协议、证据归档及配置隔离 |
| 跳过项 | `test_mysql_integration.py` 未配置专用 `SECOPS_TEST_MYSQL_URL`，未冒充通过；时间精度和过期租约另在容器脚本验证 |
| Python / JavaScript / PowerShell | compileall、node --check、启动脚本 Parser 检查通过 |
| Compose | config --quiet 通过；镜像构建、迁移和容器启动成功 |
| MySQL + Redis + Celery 集成 | 五场景由独立 Worker 执行；实际 MySQL 双精度、租约 fencing、人工复核/审批/幂等执行/回查、撤回重申、事件续取、证据导出及跨进程指标通过 |
| 进程崩溃恢复 | 探测子进程在首条观察 checkpoint 后 `os._exit(17)`；加速该测试运行租约后由 dispatcher 重投，复用已存证据完成调查 |
| 数据库备份恢复 | mysqldump 恢复至单独探测库；7 表、154 行及迁移版本一致，探测库清理、原库未覆盖，服务恢复 |
| OTel | 控制台 span 实际导出并按 JSON 解析成功；不是 OTLP/企业链路平台联调 |
| 独立审查 | 修复连接器切换、字符串布尔误判、多窗口证据覆盖、配置混用、关闭事务、失效审批阻塞等问题；最终复查未发现新增 P1/P2，定向 28 项测试通过，额外指标授权隔离检查通过 |

最后集成样本使用 simulated 模型和 simulated 连接器。测试过程发现 MySQL 默认 FLOAT 不能精确保存 epoch 秒，已通过 0002 迁移改为 Float(53)，并验证亚毫秒精度。审批撤回只适用 pending/approved，不允许对 executing/unknown 直接撤回以重放。

恢复探测最初与 dispatcher 抢占同一测试任务导致失败，已将脚本拆成停止 dispatcher 后 prepare、启动后 finish 两段，随后通过。这是测试准备时序修正，不隐藏失败记录。OTel 首次输出捕获方式不匹配库默认 stdout，改用子进程捕获后通过。

## 未验证或尚未实现的生产条件

1. 未获得真实模型、IdP、日志平台、WAF/IAM 接口和授权凭据；仅适配与模拟协议验证，没有真实供应商效果/兼容性记录。
2. 企业 gateway 需要具体厂商映射，幂等、外部状态查询、到期解除及补偿都必须在授权测试环境验证。平台没有通用提前解除接口。
3. 浏览器工具访问被拒绝，未绕过权限使用其他自动化手段。API 和 JS 静态验证不能代替页面交互、布局、可访问性验收。
4. 本次备份测试只覆盖 MySQL 逻辑恢复，不含证据卷/S3 的独立环境恢复；未测生产 RPO/RTO、故障全集和负载/P95。
5. GitHub Actions 文件已提供，但本目录无 Git 仓库及远端，未实际触发远端 CI、提交或 PR。
6. 旧 SQLite 记录未迁移；保留原入口和数据，尚无旧数据导入器。
7. 管理配置采用环境/文件；未实现可视化配置发布、完整观测指标、机器身份告警 webhook、自动化数据保留任务、OTLP/监控平台整合。
8. 证据当前以结构化脱敏归档为主；没有全文 RAG、长期记忆、向量库或完整原始大附件管道。分页完整性采取保守策略，仍需真实源的覆盖率约定。

## 文件与复现

入口与使用方式见根目录 README，配置及接口合约见 `docs/platform-operations.md`。新增主目录为 `app/agent/`、`app/platform/`、`migrations/`；前端为 `app/static/platform.*`，旧 Demo 保留。

本机 Python 3.12 虚拟环境为 `.venv`，默认系统 Python 3.7 不适用。pytest 使用工作区 `.test-tmp/` 避免系统临时目录权限问题。新平台默认 http://127.0.0.1:18081；临时 18082 预览进程已停止。

源码回退备份：`.backups/pre-platform-20261017-120757.zip`。数据库探测备份：`.backups/secops_restore_probe_78aac02960a8.sql`，仅本地保留且不提交。数据库与归档可能含敏感信息，应按企业策略保护。
