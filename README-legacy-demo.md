# SecOps · 企业安全告警研判与响应协同 Demo

一个可以实际操作的 Python 全栈演示：**告警接入 → LangGraph 调查 → 人工复核 → 审批 → 模拟执行 → 回查关闭**。

前端包括运营工作台、事件调查、结构化证据、工具执行轨迹、工单中心、审计日志、数据连接和演示角色切换。无需模型密钥，无需单独安装 Node.js，也无需下载前端 CDN 资源。

> 当前连接器、日志、威胁场景和安全处置全部是本地模拟。界面已明确标注。LangGraph、API、SQLite 持久化、状态机、权限检查、审批校验和 SSE 通知是真实运行的。它是项目 Demo，不是可以直接接管生产安全系统的成品。

## Docker 一键启动

前提：安装 Docker Desktop、切换到 Linux containers，并确保当前用户可以访问 Docker Engine。首次构建需要联网拉取 Python 镜像和依赖。

```powershell
cd D:\PythonProject\Agent_yizhiyun
docker compose up -d --build
```

访问 **http://localhost:8080**。

Windows 也可双击 `start.cmd`，或者运行：

```powershell
.\start.ps1
```

脚本会构建并启动容器，等待健康检查通过后打开浏览器。

```powershell
docker compose ps
docker compose logs --tail 100
docker compose down
```

`down` 保留数据，重新启动后可继续查看事件、工单和审计记录。数据保存在命名卷 `yizhiyun-secops_secops-data`。容器使用非 root 用户，仅向本机回环地址开放端口。

端口占用时，将 `.env.example` 复制为 `.env`，设置 `SECOPS_PORT=8081`，再启动。PowerShell 环境变量优先于 `.env`。

## 放到指定目录并部署

如果拿到的项目尚未位于目标目录，在项目根目录执行：

```powershell
.\install-to-target.ps1
```

默认复制到 `D:\PythonProject\Agent_yizhiyun`，再调用 Docker 启动脚本。目标目录必须为空，脚本不会覆盖已有工作，也不会复制本地运行数据库和缓存。

只复制、不启动：

```powershell
.\install-to-target.ps1 -NoStart
```

当前开发会话无权写入目标目录，也无权访问 Docker 引擎，因此源码在可写工作区创建。此限制不等于用户自己的 PowerShell 无法操作目标目录。容器实际构建与启动状态请参见 `docs/verification.md`，不要把 Compose 配置校验当作容器已启动。

## 三分钟体验完整流程

1. 保持右上角「安全分析员」，打开订单接口 SQL 注入事件，点击「开始调查」。
2. 查看「执行轨迹」中三个逻辑 Agent 的真实工具调用，再查看「证据链」和「调查报告」。
3. 点击「人工复核」，填写判断依据，确认为恶意活动。系统生成工单，进入待审批。
4. 切换为「安全负责人」，审批指定目标、范围和持续时间。可驳回要求重新复核。
5. 切换为「运维执行员」，执行模拟措施。重复执行不会产生第二条执行记录。
6. 切回「安全分析员」，点击「回查并确认关闭」。只有检查通过才会关闭事件及工单。
7. 查看工单中心和审计日志，下载事件报告 JSON。

角色选择是本地演示机制，任何访问本机页面的人均可切换角色。它不等同于生产身份认证或真实用户身份分离。

## 五个预置场景

| 场景 | 预期行为 |
|---|---|
| 订单接口疑似 SQL 注入 | 恶意请求，不能仅凭 HTTP 200 声称攻击成功 |
| 异常登录后敏感导出 | 账号调查、异常操作证据、模拟冻结账号 |
| 授权扫描 | 同时核验来源、资产范围和授权时间，走人工归档 |
| 应用日志缺失 | 保持待核实；不能按“没有记录”判断无风险 |
| WAF 已拦截恶意探测 | 仍属于恶意活动，已拦截不等于误报 |

可通过「接入演示告警」重复创建新的独立演示事件。事件总量限制为 200 条。

## 已实现的工程约束

- FastAPI 后台任务执行实际 LangGraph StateGraph：上下文收集、证据调查、报告生成。
- SQLite 事务同时更新事件状态和业务审计；连接结束后显式关闭。
- 乐观版本校验；复核/审批弹窗绑定打开时的事件版本，避免过期画面静默提交。
- 审批绑定具体操作参数哈希，30 分钟后过期，参数变化必须重新审批。
- 执行结果幂等；模拟措施有有效期。过期或回查失败可发起新调查，旧证据及处置记录归档保留。
- 进程重启后，已完成状态保留；中断中的调查转为可见的人工接管状态，需手动重新调查，不会宣称自动续跑。
- SSE 推送状态版本，刷新页面后仍可读取持久化数据。
- Cookie 演示会话、同源变更检查、工具白名单、输入校验、HTML 转义与 CSP。

当前工具通过 Python 受控注册表在 LangGraph 中调用，尚未提供独立 MCP 协议服务器，也没有独立 A2A Agent 服务。对外部数据源的连接器、企业 IAM、多租户、分布式队列、生产数据库和自动回滚，均为后续生产化工作。

## 可选模型摘要

默认使用确定性演示策略完成研判。要体验模型辅助文字摘要，可在 `.env` 配置兼容 Chat Completions 的服务：

```dotenv
SECOPS_LLM_BASE_URL=https://your-model-service.example/v1
SECOPS_LLM_MODEL=your-model-name
SECOPS_LLM_API_KEY=your-api-key
```

重新运行 `docker compose up -d --build`。配置密钥后，调查会将本项目的模拟证据片段发送到你指定的服务。密钥不进入前端、不写入审计和导出报告，`.env` 不进入 Docker 构建上下文。

模型只起草待人工核验的摘要，不改变规则结论、审批或执行权限；服务失败会回退。离线模式与可选模型调用不要混同于完全自主的生产安全 Agent。

## 不使用 Docker 的开发运行

需要 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 18080
```

访问 http://127.0.0.1:18080。API 参考：`/api/docs`；OpenAPI：`/openapi.json`；健康检查：`/healthz`。

```powershell
python -m pytest -q
```

## 目录

```text
app/main.py           HTTP API、演示会话、SSE、静态页面
app/service.py        权限与事件状态流转、审批和回查
app/workflow.py       LangGraph、规则判定、可选模型摘要
app/tools.py          只读模拟连接器
app/storage.py        SQLite 事务与审计
app/seed.py           场景数据
app/static/           中文响应式前端，无构建步骤
tests/                工作流和 API 回归测试
docs/                 设计、实现计划、验证与已知限制
Dockerfile            Python 3.12 非 root 镜像
compose.yaml          回环端口、持久卷、健康检查
start.ps1 / start.cmd Docker 一键启动
install-to-target.ps1 安全复制至目标目录并启动
```
