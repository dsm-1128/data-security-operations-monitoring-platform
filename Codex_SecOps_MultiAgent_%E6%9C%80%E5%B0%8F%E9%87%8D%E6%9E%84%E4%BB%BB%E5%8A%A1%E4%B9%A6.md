# 交给 Codex：SecOps 平台最小侵入式 Multi-Agent 重构任务书

> 请在**当前仓库原地完成代码重构并运行验证**，不要只给设计文档。先读现有文件与测试，确认实际结构后再修改。业务和安全边界以仓库代码为准；本任务书中的新架构是**目标实现**，不是声称仓库已实现。

## 1. 目标与不可改变的边界

现状：新平台（`app/platform_app.py`）使用 `app/platform/runtime.py` 调用 `app/agent/loop.py` 的**单 Agent LangGraph tool-calling 循环**。`app/agent/model.py` 提供 `ModelClient` / `SimulatedModel`；`app/agent/tools.py` 提供六类只读 `ToolRegistry` 工具；`app/agent/evidence.py` 的 `policy_report` 执行确定性结论；`RunStore` 以 JSON state 快照、租约/fencing 恢复运行。旧 Demo 在 `app/main.py` / `app/workflow.py`，不属于本次重构范围。

目标：以**最小改动**实现可以通过运行轨迹证明的 **Supervisor（主 Agent）+ 按业务分工的子 Agent**，具备真实角色隔离、独立模型决策、工具权限约束、结构化交接、动态调度与失败转人工。先完成单 Worker 中**串行子 Agent 委派**，本轮不做并发，也不为增加数量而拆 Agent。

必须保留：原 FastAPI/API/SSE/前端、MySQL / Redis / Celery / Outbox、OIDC、审批执行与回查状态机、现有证据结构与 `policy_report`、成本/时间预算、checkpoint、租约/fencing、取消、模拟与真实模型/连接器两种模式。**不改数据库表结构、无需新增数据库迁移、默认不加第三方依赖。**

用配置开关 `SECOPS_AGENT_ARCH=single|multi` 灰度启用；默认 `single`，保证现有测试与行为不变。更新配置验证和 `config_fingerprint`，避免同一调查在单/多架构之间恢复时混用快照。给出 `.env.platform.example`（若存在）与 README 的启用说明；`compose.platform.yaml` 当前使用 `env_file: .env.platform`，不要擅自覆盖环境值。

## 2. 子 Agent 的业务边界（1 主 + 4 子，按需触发）

| 角色 | 独立业务目标 | 允许读取的现有工具 | 不得做的事 |
|---|---|---|---|
| `Supervisor` | 理解事件、Planning、选择/重调度子 Agent、检查任务覆盖、聚合证据、最终提交 | 只能委派下面四个 Agent，不直接越权调用企业工具 | 不直接执行处置，不自行给出未经证据验证的最终结论 |
| `TriageAgent` | 资产与授权核验，判断是否要进一步调查 | `asset_context`, `authorization` | 不下最终恶意/误报结论 |
| `WebInvestigationAgent` | WAF / SQL 注入 / Web 请求与应用审计的事实调查 | `security_logs`, `business_audit` | 不调用登录认证工具，不做写操作 |
| `IdentityInvestigationAgent` | 异地登录 / 账号异常 / MFA / 敏感导出事件调查 | `auth_logs`, `business_audit`, `security_logs` | 不直接冻结账号 |
| `ResponseAgent` | 用现有处置手册提出措施与人工审批建议 | `playbook`（可读取已收集的结构化证据） | 不执行封禁、审批或覆写风险规则结果 |

**注意：** Agent 与 Tool 有区别。每个子 Agent 必须有：① 独立 System Prompt；② **严格收窄的 `ToolRegistry`**；③ 至少可多轮 `model → tool → observation → model` 自主完成专业子任务；④ 结构化 `result`。单纯写四个普通函数并按固定顺序调用、或只给同一个 Loop 改四个名字，不算完成。

六个工具仍仅使用仓库现有：`asset_context`, `security_logs`, `auth_logs`, `business_audit`, `authorization`, `playbook`；**禁止新增虚构外部数据源、RAG、向量库或写操作工具**。工具查询由原 Registry 执行，继续做资产、账号、源 IP、路径、时间范围校验以及证据事实校验。

## 3. 动态委派规则与真实模型/离线模拟

主 Agent **不应硬编码“初筛→Web→Identity→Response”全量固定链路**。主 Agent 基于事件字段、已取得的证据、未满足的验证要求和子 Agent 返回值决定下一个 Agent；一个事件可以跳过不相关的子 Agent，也可以要求某个 Agent 再调查。在 `live` 模式使用**真实模型做结构化委派决策**，由确定性 policy 校验它是否有权限、是否充分、是否超预算；在 `simulated` 模式使用同一套协议的**确定性模拟 Supervisor 与模拟子 Agent**，基于 `category`/`rule` 和**已获取的证据**作决策，不能依赖 `case['fixture']` 直接偷看答案。不得假称模拟策略是真实 LLM。

建议场景路径（是期望覆盖，不要求严格一致；要能解释为何路由）：

- `web_attack`：初筛→Web 调查→处置建议→统一报告；最后 `malicious`。
- `authorized_scan`：初筛→Web 调查核验授权与行为→处置建议或审核建议；最后 `authorized`，不自动执行封禁。
- `blocked_attack`：初筛→Web 调查→建议；恶意请求**已拦截不等于误报**，`malicious` 且 `defense=blocked`。
- `account_anomaly`：初筛→账号调查（使用 `auth_logs` / `business_audit` / `security_logs`）→处置建议；最后 `malicious` 且确认敏感导出 `impact=confirmed`。**无需因为叫 Multi-Agent 就强制再运行 Web Agent**；有额外 Web 证据需求才委派它。
- `missing_logs`：初筛→Web 调查发现应用审计 `complete=False`→`needs_attention`，**不能生成虚假已完成报告**。

最终验证门槛**不得降低**：在进入 `waiting_review` 前，至少拥有已引用且有效的 `security_logs`、`authorization`、`business_audit`；对于 `category=identity` 还要 `auth_logs`；所有生效查询必须完整且引用 ID 必须来自真实获取的证据。复用原 `AgentLoop._finish` 的证据门槛或提取为公用验证函数；仍用 `policy_report` 计算 `verdict`、`impact`、`defense`，LLM 只生成解释或建议，不能覆写规则结论。

## 4. 具体改造建议（优先修改这些文件）

1. `app/agent/model.py`：给 `ModelClient` 增加**向后兼容**的自定义 `system_prompt` 支持（保持原有 `decide(messages, tools, remaining_tokens)` 调用不破坏）。Supervisor 的委派提示词与各子 Agent 的提示词分开；不要输出隐藏推理过程；模型响应做 Pydantic 结构化验证。保留原 `SimulatedModel` 的旧单 Agent 行为。
2. `app/agent/tools.py`：加 `ToolRegistry.subset(allowed_names)` 或等价安全包装，只暴露并且只允许执行白名单中的工具。**不仅 definitions 过滤，execute 也必须拒绝越权工具**；原 Query 参数/跨资产检查必须仍生效，不能给模型直接注入 handlers 引用绕过。
3. `app/agent/loop.py`：尽量**复用现有单步处理/错误处理/checkpoint**。通过新增可选策略或提取共用循环，支持“子任务完成只返回 `SubAgentResult`，但不改变平台运行状态到 `waiting_review`”，默认行为仍完全保持原样。不得让每个子 Agent 重置全局预算，也不得把中间结果写入正式 `report`。
4. 新建 `app/agent/subagents.py`：四个 Agent 的 Prompt、白名单、统一执行封装、结构化输出。建议输出字段 `agent`, `status(completed|needs_attention)`, `summary`, `evidence_ids`, `need_more`, `reason`；引用 ID 必须校验属于全局已有证据，不能编造。
5. 新建 `app/agent/supervisor.py`：主 Agent 的 Planning / delegation / replan、子 Agent 调度、最终必要证据检查、调用 `policy_report`、预算耗尽 / 异常转人工。优先通过 LangGraph 条件边或子图实现 supervisor 与 subagent 转换，**不是固定 DAG**。只需串行委派即可，但每一步在 trace 中可看出谁计划、谁被调用、谁调用了什么工具及为什么结束。
6. `app/platform/runtime.py`：保留同一模型、registry、checkpoint、cancelled、trace/span 初始化与归档；仅在实例化 loop 处依据 `settings.agent_arch` 选择 `AgentLoop` 或 `MultiAgentSupervisor`。继续支持 `model_factory` / `registry_factory` 的测试注入；不改变 `process_run` 入参和返回结构。
7. `app/platform/config.py`：新增 `agent_arch`、环境变量读取、枚举校验并纳入 `config_fingerprint`。如果补充配置到文档/示例，清楚写明默认仍单 Agent，开发模式设 `SECOPS_AGENT_ARCH=multi` 可体验。
8. `app/platform/runs.py`：**原则上不修改数据库持久化逻辑**，只在确有必要时最小改 `resume`，保证人工补充后的消息也进入 Supervisor / 当前子 Agent 上下文，并且不会清掉已取得证据或重复执行已完成工具。
9. `tests/test_multi_agent.py`（新增）等：证明路由确实动态、子 Agent 权限隔离、跨阶段 checkpoint、取消、失败接管和原状态兼容。

如果实际实现更简洁，可调整文件划分，但必须在交付说明中解释原因。**不要大规模重写 `app/platform/*`，不要引入 CrewAI / AutoGen，不要修改旧版 `app/workflow.py`。**

## 5. 状态与恢复：这部分是硬性安全约束

- 公共全局 state 仍按原 `RunStore` 保存为**可 JSON 序列化**数据；在其下新开 `multi_agent` 命名空间，记录 `phase`, `active_agent`, `plan`, `subagent_states`, `results` 和可恢复的 `pending_delegation` 等，不更换原 `id`, `status`, `case`, `evidence`, `trace`, `messages`, `rounds`, `tool_count`, `tokens`, `limits`, `active_seconds`, `query_cache` 等既有核心字段。
- 子 Agent **中途交接时，顶层 `status` 始终是 `running`**；只有完整调查和证据门槛通过才变 `waiting_review`，不可因某个子 Agent 完成而暂时写 `completed`/`subagent_done` 到顶层——否则现有 `RunStore.save` 可能释放 Worker 租约，使后续持久化失败。
- 子 Agent 各有隔离的消息上下文，避免混淆 tool_call_id；共用全局 evidence、query_cache、trace 和**全局** token/轮次/工具数/秒数预算，不能按 Agent 分别重置预算从而绕过上限。请估算多 Agent 额外规划轮次，必要时在**配置允许范围内**明确建议调整预算，不能默默提高生产上限。
- checkpoint 不仅覆盖调用具体工具后的 observation，也必须覆盖主管选择 Agent / 子 Agent 完成等**阶段切换**；崩溃恢复不能再次请求已存档的相同工具结果。保持 `fence`、取消检查与原保存异常直接向外传播的策略。
- 如果已有 `pending` 工具调用，恢复时先按原工具名与当前 Agent 白名单校验；模型调用/校验失败、跨资产调用、工具结果 `complete=False`、循环不前进、预算耗尽时保守转 `needs_attention`，记录可理解原因，不能自动跳过缺失证据。
- 复用已取得的完整查询证据，避免 Web Agent 与 Identity Agent 重复调用相同工具；重复调用的跨 Agent 缓存命中不可被误当作“取得了新证据”。
- 保留租约/fencing、原子保存和审批参数绑定，不得让 Agent 直接执行处置或模拟审批成功。

## 6. 测试和验收（必须执行）

新增自动化测试至少覆盖：

1. `single` 默认模式的 `AgentLoop` 旧测试全部通过，返回结构兼容。
2. `multi + simulated` 五个现有 `SCENARIOS` 均跑通；前三类和账号异常满足正确 `verdict`/`impact`/`defense` 与 `waiting_review`；日志缺失为 `needs_attention` 且 `report is None`。
3. `web_attack` 不委派 Identity Agent；`account_anomaly` 调用 Identity Agent，并能取得 `auth_logs`；允许按实际证据动态增补，不用所有子 Agent 都跑一遍。
4. 子 Agent 只能看到和执行自身许可工具，即使模型故意请求另一子 Agent 的工具，也不能执行。
5. 子 Agent 的 `report.evidence_ids` 不能包含虚构 ID；全局必需证据缺失不能完成；恶意已拦截不误判为误报；授权扫描不能直接被当作恶意需封禁。
6. 经过至少一次**工具 observation 后**和一次**subagent handoff 后**的 checkpoint 模拟崩溃恢复，证据不会重复写入，同一完整查询不重复执行，最终能完成。
7. 测试跨资产越权、运行取消、预算耗尽、模型异常、连接器异常和人工补充 `resume`，都沿用原防护。
8. 保持原 HTTP/API/SSE、MySQL 测试与审批执行流程；旧 Demo 测试继续通过。
9. 专门测试 `config_fingerprint`：单 Agent 旧快照不能在多 Agent 模式无声恢复，反之亦然。
10. trace 中能观察到 `supervisor_plan/decision`, `subagent_start`, `subagent_tool`, `subagent_end`（名称可调整）以及每次委派的原因；SSE/前端原有 trace 展示不应报错。

执行（按实际环境可用性运行，记录 skipped/失败，不得声称未执行的验证已通过）：

```bash
python -m pytest -q --basetemp=.test-tmp/multi-agent-run
python -m compileall -q app
node --check app/static/platform.js
```

如果能使用 Docker，再做：

```bash
docker compose --env-file .env.platform -f compose.platform.yaml config --quiet
docker compose --env-file .env.platform -f compose.platform.yaml up -d --build
```

五场景端到端校验优先 simulated 环境，**不要访问真实生产设备/数据**。若 `SECOPS_TEST_MYSQL_URL` 未配置，保留相关测试的真实跳过说明。

## 7. Codex 执行顺序与交付格式

1. 先列出发现的真实调用链，以及你预计改动的文件（不要立刻重写）。
2. 做最小实现：工具作用域 → 不同提示词 → 专业子 Agent → 主 Agent 编排 → runtime 开关；每一步保持可测试。
3. 新增针对性测试，再运行已有完整回归与适用的 Docker 验证。
4. 给出最终提交摘要：**修改文件列表、架构决策、五场景实际 trace、通过/跳过测试数量、兼容性风险和未验证项**。
5. 给出可复现启用方式：单 Agent 保持默认，如何把 `SECOPS_AGENT_ARCH=multi` 写入 `.env.platform` 后重启 api/worker，以及如何恢复单 Agent。
6. 更新 `README.md`（和需要的设计文档），明确说明这是 `Supervisor + 子 Agent`，说明各 Agent 的工具边界、只读调查与人工审批机制；明确模拟数据/模型不代表真实企业上线。

**验收定义：** 同一组五个模拟事件，在 `multi` 模式下可以从现有 API 创建调查，并从轨迹中看到主 Agent 根据事件或证据委派**不同的专业子 Agent**、各子 Agent 使用受限工具独立调查、统一规则生成报告或保守转人工；原 `single` 模式行为及复核/审批/执行/回查链路保持不变；关键安全和恢复测试通过。若发现某项需求与当前实现结构冲突，以“保留已有可靠性与安全边界”为最高优先级，报告阻碍并给出最小可行替代方案，禁止静默弱化。
