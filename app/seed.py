"""Explicitly synthetic scenarios; reserved documentation IPs only."""
from copy import deepcopy
from datetime import datetime, timezone
import uuid

SCENARIOS = {
    "web_attack": {"name": "订单接口疑似 SQL 注入", "category": "web", "severity": "high", "asset": "订单服务 / 生产", "owner": "应用运维组", "ip": "198.51.100.23", "count": 126, "rule": "SQL 注入检测", "path": "/api/orders/search", "fixture": {"attack": True, "blocked": False, "complete": True, "authorized": False, "export": False}},
    "account_anomaly": {"name": "运营账号异地登录后批量导出", "category": "identity", "severity": "critical", "asset": "运营管理后台 / 生产", "owner": "业务安全组", "ip": "203.0.113.48", "count": 18, "rule": "异常登录与敏感操作关联", "path": "/admin/customers/export", "account": "ops_demo_07", "fixture": {"attack": True, "blocked": False, "complete": True, "authorized": False, "export": True}},
    "authorized_scan": {"name": "授权扫描器触发漏洞探测告警", "category": "web", "severity": "medium", "asset": "商品中心 / 预发布", "owner": "平台运维组", "ip": "192.0.2.66", "count": 842, "rule": "漏洞扫描行为", "path": "/api/products", "fixture": {"attack": False, "blocked": False, "complete": True, "authorized": True, "export": False}},
    "missing_logs": {"name": "支付接口异常请求 · 应用日志缺失", "category": "web", "severity": "high", "asset": "支付网关 / 生产", "owner": "支付应用组", "ip": "198.51.100.72", "count": 37, "rule": "可疑请求参数", "path": "/api/payment/callback", "fixture": {"attack": True, "blocked": False, "complete": False, "authorized": False, "export": False}},
    "blocked_attack": {"name": "登录接口恶意探测已被 WAF 拦截", "category": "web", "severity": "medium", "asset": "用户中心 / 生产", "owner": "平台运维组", "ip": "203.0.113.19", "count": 281, "rule": "恶意扫描与注入探测", "path": "/api/auth/login", "fixture": {"attack": True, "blocked": True, "complete": True, "authorized": False, "export": False}},
}


def now():
    return datetime.now(timezone.utc).isoformat()


def make_incident(scenario):
    item = deepcopy(SCENARIOS[scenario])
    timestamp = now()
    return {**item, "id": "SEC-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper(), "scenario": scenario, "tenant": "demo-retail", "status": "new", "created_at": timestamp, "updated_at": timestamp, "version": 0, "evidence": [], "trace": [], "report": None, "ticket": None, "action": None, "approval": None, "execution": None, "verification": None, "review": None, "error": None, "simulated": True}
