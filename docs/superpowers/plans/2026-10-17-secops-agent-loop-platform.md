# SecOps Agent Loop Platform Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. Track each task and test outcome in the execution ledger.

**Goal:** 将现有安全运营 Demo 演进为具备真实模型循环、可靠运行、企业授权和受控执行的平台。

**Architecture:** 模块化 FastAPI API + LangGraph 决策循环 + 独立 Celery Worker。MySQL 保存业务、运行快照与 Outbox；本地开发保留 SQLite。只读工具和外部写执行分别授权。

**Tech Stack:** Python 3.12, FastAPI, LangGraph 1.1.6, HTTPX, Pydantic, SQLAlchemy, Alembic, MySQL, Celery, Redis, OIDC, SSE, Docker。

**Spec:** `docs/superpowers/specs/2026-10-17-secops-agent-loop-platform-design.md`

## Global Constraints

- 保留既有五类安全场景、审批参数绑定和事件 API 的可兼容行为。
- 真实循环由模型决定工具与参数；模拟模式明确标注，不以固定策略冒充真实模型。
- 初始预算 12 轮、24 次工具调用、300 秒主动运行；Token 预算可配置。
- 企业 production 模式禁止演示身份、模拟写操作和隐式数据库建表。
- 不宣称尚未进行的真实模型、企业接口、恢复或负载验证通过。

## Review Focus

过期 Worker 提交、外部超时但已经成功、资产权限撤销、工具返回超大或恶意内容、模型连续错误和提前结束：分别在 runtime、execution、auth、tools、loop 测试中验证。

## Task 1: Model-driven loop and controlled tools

Files: `app/agent/{contracts,model,tools,loop}.py`, `tests/test_agent_loop.py`。
Interfaces: `AgentLoop.run(state, checkpoint, cancelled)`, `ModelClient.decide(messages, tools, remaining_tokens)`, `ToolRegistry.execute(call, context)`。

- [x] 写 scripted-model 测试，验证不同观察改变下一步调用、缺证据不能完成、工具权限、预算、模型异常和重复调用。
- [x] 运行测试观察缺失实现失败。
- [x] 实现工具 Schema、模型适配器、LangGraph 条件循环、完成校验、轨迹与预算。
- [x] 运行循环测试及既有回归；记录结果。

## Task 2: Durable runs and database

Files: `app/platform/{database,runs,dispatch}.py`, `migrations/`, `tests/test_runs.py`。
Interfaces: `RunStore.create/get/claim/save/events/cancel`, `dispatch_pending()`。

- [x] 测试重启恢复、租约拒绝、重复投递、Outbox、取消和事件续取。
- [x] 观察失败后实现 SQLAlchemy MySQL/SQLite 持久化、事务审计、快照与运行租约。
- [x] 增加 Alembic 迁移与显式初始化命令；不自动迁移旧数据。
- [x] 运行持久化测试和 MySQL 可用时的集成验证。

## Task 3: Identity, policy and response execution

Files: `app/platform/{config,auth,execution,connectors}.py`, `tests/test_platform_security.py`。
Interfaces: `Principal`, `authorize(principal, asset, permission)`, `ActionService.request/approve/execute/reconcile`。

- [x] 测试生产拒绝演示、权限隔离、自批、过期审批、改参、未知执行结果和稳定幂等键。
- [x] 实现 OIDC 登录、服务端会话、资产授权、受控 HTTP 连接器和执行台账。
- [x] 加入配置验证、凭据引用、固定连接器地址和输出限制。
- [x] 运行安全和外部 HTTP 合约测试；缺真实企业接入单独列出。

## Task 4: API, Worker and workbench

Files: `app/platform/{api,worker}.py`, `app/main.py`, `app/static/platform.{html,js,css}`, `tests/test_platform_api.py`。

- [x] 测试运行创建/取消/继续、SSE 续取、未授权查询与审计。
- [x] 将运行内核接入 API 与 Celery，提供独立平台工作台，原有 Demo 保留在明确入口。
- [x] 展示模式、轮次、预算、工具轨迹、证据、接管原因、审批和执行结果。
- [x] 运行 API、JS 语法验证。
- [ ] 浏览器端到端验证：浏览器访问被拒绝，未采用替代通道绕过。

## Task 5: Deployment, operations and final review

Files: `compose.platform.yaml`, `Dockerfile.platform`, `requirements*.txt`, `.env.platform.example`, `README.md`, `docs/platform-operations.md`, `docs/platform-verification.md`。

- [x] 提供 MySQL/Redis/API/Worker/dispatcher 服务、迁移步骤、readiness、备份恢复与发布流程。
- [x] 全量 pytest、Python/JS 语法、Compose 配置与可用环境集成验证。
- [x] 对最终改动执行独立代码审查，修复影响行为的问题并回归。
- [x] 更新交付记录，逐项区分已实现、已验证与企业环境待联调项。

## Execution ledger

- 2026-10-17：用户明确“执行”，按当前会话内执行；不再次请求相同范围授权。规格是实现依据。
- 初始目录不是 Git 仓库；先建立源码备份。默认 Python 3.7 不适用，定位 3.12；Docker Engine 未运行。

- 已建立源码 ZIP 备份；找到 Python 3.12.14 并创建 .venv。未初始化 Git 或修改远端。
- Task 1：模型 HTTP 工具协议、动态循环、证据约束和预算实现；补充严格布尔、多窗证据保留、最终调用超时和账号行为独立证据回归。
- Task 2：SQLAlchemy/MySQL、Outbox、快照与 fencing 实现；发现 MySQL FLOAT epoch 舍入问题，0002 迁移修复并容器验证。
- Task 3：OIDC/服务端会话、权限、连接器、审批执行实现；独立审查推动修复配置隔离、连接器切换、原子回查和失效审批撤回。
- Task 4：平台独立入口、工作台、SSE、证据下载与指标实现；旧 Demo 保留。API/JS 已验证，浏览器权限拒绝使视觉/交互验收未完成。
- Task 5：Compose 六服务（含一次性 migrate）、CI 配置、随机开发配置启动脚本、运维和验证文档完成。本地 71 passed / 1 skipped；独立 Worker 五场景、审批闭环和恢复通过。
- 恢复演练：首条观察落库后终止探测进程，dispatcher 重投并复用证据。MySQL 备份恢复至隔离库，7 表/154 行匹配；未验证证据存储独立恢复。
- 最终独立代码审查：无新增 P1/P2 阻断，定向 28 项通过并检查指标授权隔离。真实接入缺失与下述未实现范围不能用代码审查结果替代。

## 尚未达到的完整规格

当前完成可运行基础版本，完整企业上线仍未完成：真实模型/企业 SSO/厂商数据和处置源联调；旧数据导入；厂商补偿和到期解除验证；可视化配置发布；完整观测/数据保留、机器身份告警入口；浏览器、负载、生产恢复与 SLA 验收。详见 docs/platform-verification.md，不将未完成项包装为已上线能力。
