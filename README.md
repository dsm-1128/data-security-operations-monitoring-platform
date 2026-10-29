# 数据安全运营监控平台

面向企业安全运营的 Agent Loop 平台：**告警 → 模型选择工具 → 读取证据 → 继续调查 → 人工复核 → 审批 → 受控执行 → 回查关闭**。

调查内核使用 LangGraph 条件循环。真实模型通过 tool calling 选择下一步工具和参数；每次行动及观察分别保存，Worker 中断后从已提交快照恢复。保留显式离线模拟模式，便于无密钥验收流程。

**当前交付是可运行的平台基础版本。** MySQL、Redis、独立 Worker、运行恢复与模拟处置闭环已通过本机集成验证。尚未连接企业真实模型、SSO、日志平台或安全设备，不能据此认定达到生产上线标准。详见[验证记录](docs/platform-verification.md)。

## 启动

需要 Docker Desktop 的 Linux containers 引擎。项目根目录执行：

```powershell
.\start-platform.ps1
```

首次启动生成随机开发密码；已有 `.env.platform` 不覆盖。访问 **http://127.0.0.1:18081**。已有配置也可直接运行：

```powershell
docker compose --env-file .env.platform -f compose.platform.yaml up -d --build
docker compose --env-file .env.platform -f compose.platform.yaml ps
```

默认明确使用模拟模型与模拟连接器。分析员接入场景并调查，复核为恶意后申请处置；切换负责人审批，运维执行员执行，最后由分析员回查关闭。缺少日志会转人工；审批过期后可撤回未执行申请并重新提交。

## 技术栈

| 层次 | 已采用技术 / 能力 |
| --- | --- |
| 服务端 | Python 3.12、FastAPI、Pydantic、HTTPX |
| Agent 内核 | LangGraph 1.1.6、Tool Calling、受控 Tool Registry、多轮 observation、预算和完成校验 |
| 存储 | MySQL 8.4、SQLAlchemy、Alembic；SQLite 用于本地测试 |
| 调度与恢复 | Celery、Redis、事务 Outbox、租约与 fencing token、应用级快照 |
| 身份与治理 | OIDC Code + PKCE、服务端会话、岗位与资产授权、审批参数绑定、幂等执行台账 |
| 工作台 | HTML / CSS / 原生 JavaScript、持久事件 + SSE、轨迹与证据下载 |
| 证据归档 | 共享卷或 S3 兼容对象存储、SHA-256 完整性校验 |
| 观测与交付 | Prometheus 格式持久指标、OpenTelemetry console spans、Docker Compose、pytest、GitHub Actions 配置 |

模型 API、企业网关和 OIDC 适配代码已实现并进行模拟协议测试；具体供应商接入仍需联调。当前没有引入 Vue、TypeScript、向量数据库、RAG、MCP 或 Kubernetes。默认使用单 Agent 动态循环，也可通过下述配置开启 Supervisor + 专业子 Agent。

## 可选 Multi-Agent 调查

`SECOPS_AGENT_ARCH=single|multi`，默认 `single`。`multi` 使用 LangGraph 条件循环串行委派，Supervisor 根据事件、已取得证据和专业结果选择调查角色或要求补查，不固定执行所有角色。

| Agent | 目标与只读工具边界 |
| --- | --- |
| Supervisor | Planning、委派、重新规划、覆盖检查和汇总；只可调用 `delegate_agent`，不直接读取企业工具 |
| TriageAgent | 资产与授权核验；`asset_context`、`authorization` |
| WebInvestigationAgent | WAF/Web 请求与应用审计；`security_logs`、`business_audit` |
| IdentityInvestigationAgent | 登录、MFA、敏感导出关联调查；`auth_logs`、`business_audit`、`security_logs` |
| ResponseAgent | 读取手册提出措施和人工审批建议；`playbook` |

每个子 Agent 有独立 System Prompt、消息上下文、受限 ToolRegistry 和多轮 model → tool → observation 循环。`live` 调用真实模型生成结构化委派与专业工具决策；`simulated` 使用明确标识的确定性离线模型，仅根据公开事件字段与已获取证据决策，不读取 fixture 答案。模拟数据、模拟模型与模拟处置均不代表真实企业联调。

所有 Agent 共享轮次、工具、Token、主动运行时间预算、证据和完整查询缓存。`multi_agent` 快照记录规划、当前子 Agent、待办委派、隔离上下文和结构化结果；工具 observation 与交接均 checkpoint，保持原租约/fencing 和取消机制。人工补充沿用现有 resume 入口与新一轮预算授权，保留已有证据并传入 Supervisor/当前子 Agent。子 Agent 结束时顶层仍为 `running`，正式报告仅在原证据门槛通过后生成；verdict/impact/defense 始终由 `policy_report` 计算。Agent 均不审批或执行封禁/冻结，原人工复核、审批参数绑定、受控执行与回查流程保持不变。

在 `.env.platform` 中添加或修改以下键后重启（不要重复追加同名键）：

```dotenv
SECOPS_AGENT_ARCH=multi
```

```powershell
docker compose --env-file .env.platform -f compose.platform.yaml up -d --build api worker dispatcher
```

五个简单离线场景在默认 12 轮内完成或转人工。多次补查、串行单工具决策或真实模型可能需要更多轮次；可显式配置 `SECOPS_MAX_ROUNDS=24`（允许 1–48），并根据实际 Token 使用配置预算，系统不会自动增加生产上限。`SECOPS_MAX_SECONDS` 仍最多 300 秒。

恢复单 Agent：将该键改为 `SECOPS_AGENT_ARCH=single`，执行同一重启命令。架构参与 `config_fingerprint`：切换前后不得在同一调查混用快照；旧调查需恢复原配置才能继续，或创建新调查。原 single fingerprint 保持兼容。

轨迹展示 `supervisor_decision`、`subagent_start`、`subagent_tool`、`subagent_end`、Agent 名、工具、缓存命中和委派原因。可在 multi + simulated 容器运行 [验证脚本](scripts/verify_multi_agent.py)，导出五场景的实际 API 轨迹并验证 SSE 续传和模拟审批闭环。完整结果见 [Multi-Agent 验证记录](docs/multi-agent-verification.md)。

## 代码与验证

- `app/agent/`：模型、工具、证据规则与 Agent Loop。
- `app/platform/`：身份、运行台账、队列、执行、归档、API 与指标。
- `app/platform_app.py`：平台入口；`app/static/platform.*`：工作台。
- `migrations/`、`compose.platform.yaml`、`Dockerfile.platform`：迁移与部署。
- `tests/`、`scripts/verify_platform.py`、`scripts/verify_recovery.py`：回归和容器集成验证。

使用 Python 3.12 的虚拟环境：

```powershell
python -m pip install -r requirements-dev.txt -r requirements-platform.txt -c requirements-platform.lock
python -m pytest -q --basetemp=.test-tmp/local-run
python -m compileall -q app
node --check app/static/platform.js
```

Windows 若系统临时目录不可用，每次测试使用新的工作区 `--basetemp` 路径。MySQL 专用测试由 `SECOPS_TEST_MYSQL_URL` 启用，不要指向生产库。

## 文档和旧版

- [项目流程架构图：整体架构、业务闭环与 Agent Loop](docs/项目流程架构图.md)
- [部署、企业配置、网关合约及恢复操作](docs/platform-operations.md)
- [实际验证与未完成的上线条件](docs/platform-verification.md)
- [设计规格](docs/superpowers/specs/2026-10-17-secops-agent-loop-platform-design.md)
- [实施计划与交付记录](docs/superpowers/plans/2026-10-17-secops-agent-loop-platform.md)

原固定流程 Demo 保留：`app.main:app`、`compose.yaml`、`start.cmd` / `start.ps1`，见 [README-legacy-demo.md](README-legacy-demo.md)。新平台使用不同入口和数据存储，未自动导入旧 SQLite 记录；旧文档的环境限制是历史记录，不代表本次平台验证结果。
