# SecOps Multi-Agent 实际验证记录

日期：2026-10-17（Asia/Shanghai）。本记录仅覆盖本地模拟业务数据、离线确定性模型和模拟处置，以及真实运行的本机数据库/队列/HTTP 服务。没有连接企业真实模型、SSO、日志平台或安全设备。

## 修改文件清单

| 文件 | 修改目的 |
| --- | --- |
| `app/agent/tools.py` | `subset` 同时限制 definitions/execute；提取参数、目标范围验证供缓存前复用 |
| `app/agent/model.py` | 向后兼容的 `system_prompt` 和独立 Prompt 客户端副本 |
| `app/agent/loop.py` | 原循环抽取 `step`，复用错误、预算、缓存、证据、完成门槛；缓存前校验权限并标识命中 |
| `app/agent/subagents.py`（新增） | 四角色独立目标/Prompt/工具白名单/多轮决策/消息上下文及 Pydantic `SubAgentResult` |
| `app/agent/supervisor.py`（新增） | LangGraph 条件调度、结构化委派、重新规划、阶段快照、人工补充与统一报告 |
| `app/platform/config.py` | 默认 single 的 `SECOPS_AGENT_ARCH`、枚举验证、架构 fingerprint 隔离 |
| `app/platform/runtime.py` | 仅在选择 Loop 时依据 agent_arch 分流，保留工厂注入/归档/trace/checkpoint/cancelled |
| `app/platform/api.py` | 新子 Agent 消息与原顶层消息一样不向公开 API 暴露；模拟报告显示正确引擎名称 |
| `app/static/platform.js` | 轨迹显示 Supervisor/专业 Agent、工具和完整查询缓存命中 |
| `tests/test_multi_agent.py`（新增） | 44 项动态路由、权限、证据门槛、异常、预算、恢复、live 协议、API 和人工补充测试 |
| `tests/test_platform_api.py` | 原六项 API/复核/审批执行/证据归档测试分别运行 single 和 multi |
| `scripts/verify_multi_agent.py`（新增） | 对真实运行的 API/Worker 执行五场景、SSE 续传和模拟审批闭环，保存公开快照 |
| `scripts/verify_recovery.py` | 复用原进程崩溃恢复脚本，增加 handoff 检查点探针，默认 observation 行为保留 |
| `.env.platform.example` | 默认 single 配置及显式预算建议 |
| `README.md` | 架构、工具边界、启用/回滚、预算与模拟限制说明 |
| `docs/superpowers/plans/2026-10-17-secops-multi-agent.md`（新增） | 设计、实施与验证记录 |
| `docs/multi-agent-traces.json`（新增） | 独立容器真实 API 返回的五场景公开快照、证据及逐步 trace |
| `docs/multi-agent-verification.md`（本文件） | 验证结果与限制 |

没有修改 `app/platform/runs.py`、数据库 Schema、迁移、依赖、Compose 主配置或旧 `app/workflow.py`；`.env.platform` 保持原样。

## 架构决策和安全边界

- Supervisor 只看到 `delegate_agent` 与结束/转人工协议。各子 Agent 用独立 System Prompt 和严格 subset Registry 运行原 AgentLoop 单步，多轮 model/tool/observation 自主决定专业调查；子任务不写正式 report，也不改变顶层 running 状态。
- 共用 evidence/query_cache、全局 Token/工具/轮次/时间预算；隔离 messages/pending/stalls，缓存前重新校验当前权限。阶段、子 Agent 中间 observation、handoff 均 checkpoint，保存异常不进入模型/连接器 catch。
- 所有多 Agent 私有上下文保存在 `multi_agent` 命名空间；公开 API 继续隐去内部消息。原 `RunStore.save`、租约/fencing、Outbox、取消、复核、审批参数绑定和受控执行未改变。
- 人工 resume 沿用原授权的新一轮预算规则；由 Supervisor 同步人工消息到当前子 Agent，闭合中断的 tool_call_id，保留已取得证据和完整查询缓存。只重查缺失/失败查询。
- 正式报告复用原 `_finish` 的必要证据门槛及 `policy_report`；LLM 只给事实摘要和建议，不能覆写 verdict/impact/defense。
- single 保留原 fingerprint 算法，以兼容已有 single 快照；multi 添加架构标识。双向切换都拒绝混用原调查快照。
- 使用项目固定 LangGraph 1.1.6 和既有 StateGraph 接口；条件边语义另核对了 [官方 Graph API 文档](https://docs.langchain.com/oss/python/langgraph/graph-api)。未增加第三方依赖。

## 自动化测试

基线：71 passed、1 skipped。

最终执行（使用项目 Python 3.12 `.venv`，系统 `python` 指向其他安装，未使用该解释器）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.test-tmp/multi-agent-final --tb=short -rs
# 121 passed, 1 skipped in 12.30s
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m py_compile scripts/verify_multi_agent.py scripts/verify_recovery.py
node --check app/static/platform.js
docker compose --env-file .env.platform -f compose.platform.yaml config --quiet
```

后四项退出码均为 0。唯一 pytest 跳过项：`tests/test_mysql_integration.py::test_mysql_timestamp_precision_and_expired_lease`，原因 `SECOPS_TEST_MYSQL_URL` 未配置；没有将该项记为通过。另有下述本机真实 MySQL/Celery 集成验证，但不等同于运行了此 pytest 项。

新增测试证明：五场景、Web/Identity 差异路由、Web 敏感导出动态增补 Identity、重复调度无进展转人工、跨 Agent 完整查询缓存复用、缓存 pending 权限重校验、跨资产禁止、虚构引用/必要引用缺失禁止完成、连接器/模型异常转人工、取消、预算共享、三处崩溃恢复、人工补充以及双向 fingerprint 隔离。ModelClient 使用 HTTPX MockTransport 验证真实 wire 协议和独立 Prompt，**没有调用真实模型供应商**。

按 requesting-code-review 技能进行独立审查：审查员未修改文件，对当时版本运行相关 50 项测试通过，未发现可复现 Critical/Important 问题；之后仅增加边界测试与交付文档，没有未处理的重要审查项。当前目录无 Git 元数据，因此没有 worktree、commit 或 PR。

## Docker / MySQL / Redis / Celery / HTTP

原项目执行 `docker compose --env-file .env.platform -f compose.platform.yaml up -d --build` 成功；api/mysql/redis healthy，worker/dispatcher running，实际配置仍为 single。

将既有 `scripts/verify_platform.py` 复制进 API 容器后执行，通过：MySQL 时间精度、租约 fencing、Celery 五场景、人工复核/审批/模拟执行/回查关闭、撤回重提、事件游标续读、证据导出及持久指标。

另用 `.test-tmp/multi-compose.override.yaml` 在独立 Compose project `secops-multi-check` 运行 multi，独立 MySQL/Redis/证据卷，临时端口 18083。仅该验收项目的 api/worker/dispatcher 配置 multi，未改动主项目 `.env.platform` 和 Compose。`verify_multi_agent.py` 对实际 HTTP API 创建五次调查，后台由 Celery Worker 执行。真实 SSE 连接和 Last-Event-ID 重连、证据下载、人工复核/审批/模拟执行/回查关闭均通过。

导出结果后已停止并移除临时验收容器及网络，未删除测试数据卷；主项目保留运行且仍为 single。

### 五场景实际轨迹摘要

完整 trace、委派原因、工具证据 ID 和查询范围见 [multi-agent-traces.json](multi-agent-traces.json)。

| 场景 | 实际委派轨迹 | 状态 | verdict / impact / defense | 模型轮次 / 工具数 |
| --- | --- | --- | --- | --- |
| web_attack | TriageAgent → WebInvestigationAgent → ResponseAgent | waiting_review | malicious / not_observed / passed | 10 / 5 |
| authorized_scan | TriageAgent → WebInvestigationAgent → ResponseAgent | waiting_review | authorized / not_observed / passed | 10 / 5 |
| blocked_attack | TriageAgent → WebInvestigationAgent → ResponseAgent | waiting_review | malicious / not_observed / blocked | 10 / 5 |
| account_anomaly | TriageAgent → IdentityInvestigationAgent → ResponseAgent | waiting_review | malicious / confirmed / passed | 10 / 6 |
| missing_logs | TriageAgent → WebInvestigationAgent → needs_attention | needs_attention | 无 report；business_audit.complete=False | 5 / 4 |

Triage 调用 asset_context/authorization；Web 调用 security_logs/business_audit；Identity 调用 auth_logs/business_audit/security_logs；Response 调用 playbook。每个专业 Agent 调用前后均有模型决策。authorized_scan 没有自动执行封禁；blocked_attack 没有因已拦截而被判为误报。快照导出时首场景为 waiting_review，随后该场景在模拟审批闭环验证中回查至 completed。

### 真实进程退出与接续

在独立验收项目暂时停止 dispatcher，`verify_recovery.py --prepare` 启动子进程，提交 checkpoint 后调用 `os._exit(17)`；恢复 dispatcher 后仅加速该探针运行的租约过期，由 Redis/Celery 接续。没有终止原项目服务或生产运行。

- observation 探针 `RUN-ce9d0a2571ec407c8f1c8d2c44a97f64`：第一个 observation 后退出；恢复至 waiting_review，首证据 ID 不变，asset_context 未重复写入。
- handoff 探针 `RUN-bb838e1277f24fb49028867538184851`：TriageAgent 交接后退出，顶层仍 running；恢复至 waiting_review，已取得证据保留。

离线模型没有真实供应商费用，usage=0；10 轮不代表真实模型成本。多次补查或真实单工具策略可显式设 `SECOPS_MAX_ROUNDS=24`，允许范围仍为 1–48；本次没有默默增加任何默认预算。

## 启动与恢复 single

默认无需添加配置，仍是 single。体验 multi 时在 `.env.platform` 设置 `SECOPS_AGENT_ARCH=multi`，然后执行：

```powershell
docker compose --env-file .env.platform -f compose.platform.yaml up -d --build api worker dispatcher
```

访问主项目 `http://127.0.0.1:18081`。恢复 single 时改成 `SECOPS_AGENT_ARCH=single` 并执行同一命令。切换架构后新建调查；旧调查必须恢复原配置后才能继续。

可复现 multi 验收（先确认目标配置为 development + multi + simulated）：

```powershell
docker compose --env-file .env.platform -f compose.platform.yaml cp scripts/verify_multi_agent.py api:/tmp/verify_multi_agent.py
docker compose --env-file .env.platform -f compose.platform.yaml exec -T -e PYTHONPATH=/app api python /tmp/verify_multi_agent.py
```

## 未验证项与限制

真实 LLM、企业 OIDC/身份目录、真实日志网关、安全设备封禁/冻结、S3 供应商未做联调。MySQL 专用 pytest 项如上跳过。没有新增数据库迁移或并发子 Agent；本轮只实现单 Worker 串行委派。模型调用预算随上下文增长，真实配置需依据实际耗用显式调整。没有把模拟结果描述为企业上线能力。
