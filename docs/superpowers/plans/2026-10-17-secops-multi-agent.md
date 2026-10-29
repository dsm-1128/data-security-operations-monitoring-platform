# SecOps Multi-Agent 最小重构实施记录

**依据：** 根目录 `Codex_SecOps_MultiAgent_%E6%9C%80%E5%B0%8F%E9%87%8D%E6%9E%84%E4%BB%BB%E5%8A%A1%E4%B9%A6.md`；用户明确授权原地实现并验证。

**设计：** 保留 AgentLoop 默认行为，提取单步处理。Supervisor 与四个专业子 Agent 共用单步处理与全局预算，通过 LangGraph 条件循环串行切换。子 Agent 独立消息、Prompt、受限 Registry，只返回结构化交接；仅 Supervisor 经过原 `_finish` 与 `policy_report` 生成正式报告。阶段和工具 observation 都保存同一 JSON 快照，保存异常直接传播。

**约束：** 不增加依赖、迁移或企业数据源；不改变 API、审批、执行、租约/fencing、取消。默认 single；multi 纳入 fingerprint。以现有代码为准，不把模拟调用描述为真实联调。

## 实施与验证

- [x] 工具 subset、Prompt 定制与配置开关：先写权限和配置测试，再最小实现。
- [x] 提取共用单步；实现专业子 Agent 和动态 Supervisor；先写五场景及非法交接测试。
- [x] runtime 开关与 resume：覆盖工具/阶段崩溃恢复、权限、取消、预算、异常和人工补充。
- [x] API 五场景及审批闭环、全量 pytest、compileall、JavaScript 检查、可用 Docker 验证。
- [x] README、环境示例、实际五场景 trace 和验证报告。

## Review Focus

缓存命中之前也必须验证当前工具权限；中间交接顶层必须 running；模型异常不得吞掉 checkpoint/fencing 异常；人工补充须进入主管及当前子 Agent 且保留证据；未完整查询和虚构引用不能完成。旧 single fingerprint 保持兼容，multi 使用独立 fingerprint。

## 执行记录

当前目录无 Git 元数据；按任务书原地修改，无法使用 worktree/commit。本人执行，另按 requesting-code-review 技能进行了独立审查。最终 121 passed、1 skipped；Docker single/multi 五场景、真实 SSE 续传、模拟审批闭环和 observation/handoff 进程退出恢复通过。实际证据与环境限制见 `docs/multi-agent-verification.md`。

实施决定：没有修改 runs.py，原 resume 的人工消息由 Supervisor 同步到当前专业上下文，避免改变持久化/事务接口。增加 api.py 的私有子上下文过滤，与原顶层 messages 隐藏规则一致。保留 single 旧 fingerprint，multi 单独纳入架构标识以禁止混用。预算默认不变。没有未处理的重要审查问题。
