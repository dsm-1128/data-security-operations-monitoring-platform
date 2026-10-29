"""A constrained tool registry. Every connector in this demo is synthetic."""
from .seed import now

TOOL_NAMES = {
    "asset_context": "查询业务资产与负责人",
    "security_logs": "查询 WAF 请求与防护动作",
    "auth_logs": "查询账号认证日志",
    "business_audit": "查询应用与敏感操作审计",
    "authorization": "核验变更与扫描授权",
    "playbook": "检索内部处置手册",
}


def call_tool(name, case):
    fixture = case["fixture"]
    if name == "asset_context":
        data = {"asset": case["asset"], "owner": case["owner"], "importance": "核心业务" if "生产" in case["asset"] else "非生产环境"}
        summary = f"{case['asset']}，负责人：{case['owner']}"
    elif name == "security_logs":
        data = {"source_ip": case["ip"], "path": case["path"], "rule": case["rule"], "request_count": case["count"], "final_action": "block" if fixture["blocked"] else "observe", "suspicious_pattern": fixture["attack"], "http_status": 403 if fixture["blocked"] else 200, "trace_id": "demo-trace-" + case["id"][-6:]}
        summary = (f"{case['count']} 条账号异常行为线索，关联同一演示账号与来源。" if case["category"] == "identity" else f"{case['count']} 条关联请求；WAF {'已拦截' if fixture['blocked'] else '观察 / 部分放行'}。响应码不能证明攻击成功。")
    elif name == "auth_logs":
        data = {"account": case.get("account", "未关联账号"), "unexpected_login": case["category"] == "identity", "mfa_passed": False, "owner_confirmed_unexpected": case["category"] == "identity"}
        summary = "模拟账号在异常来源登录，账号负责人确认非本人操作；未通过 MFA。" if case["category"] == "identity" else "本次请求未关联有效业务会话。"
    elif name == "business_audit":
        data = {"sensitive_export": fixture["export"], "export_rows": 2800 if fixture["export"] else 0, "application_rejected_invalid_input": fixture["attack"] and not fixture["export"], "coverage": "complete" if fixture["complete"] else "missing"}
        summary = "发现异常账号导出 2,800 条模拟客户记录，导出任务执行成功。" if fixture["export"] else ("已查时间窗内未见成功敏感操作，应用侧拒绝非法参数。" if fixture["complete"] else "应用日志缺失：当前角色无法取得完整审计数据，不能判断业务影响。")
    elif name == "authorization":
        data = {"authorized": fixture["authorized"], "scope_matches": fixture["authorized"], "within_window": fixture["authorized"], "change_id": "CHG-DEMO-017" if fixture["authorized"] else None}
        summary = "匹配 CHG-DEMO-017，来源、预发布资产、扫描路径与有效时间均在授权范围内。" if fixture["authorized"] else "未匹配到覆盖该来源、资产与时间窗的授权记录。"
    elif name == "playbook":
        data = {"document_id": "PB-ACCOUNT-02" if case["category"] == "identity" else "PB-WEB-01", "version": "1.2", "steps": ["核验原始证据", "联系资产负责人", "审批后模拟临时限制", "复查状态与后续事件"]}
        summary = "命中已审核处置手册；执行前必须明确目标、操作范围与有效期限。"
    else:
        raise ValueError("工具不在允许列表中")
    complete = name != "business_audit" or fixture["complete"]
    title = "查询账号异常告警" if name == "security_logs" and case["category"] == "identity" else TOOL_NAMES[name]
    return {"id": "EV-" + name, "tool": name, "title": title, "source": "模拟连接器 / " + name, "at": now(), "complete": complete, "summary": summary, "data": data, "simulated": True}
