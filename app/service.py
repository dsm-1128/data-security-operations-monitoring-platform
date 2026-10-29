import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from .seed import SCENARIOS, make_incident, now
from .workflow import run_graph


class DomainError(Exception):
    def __init__(self, message, code=409):
        self.code = code
        super().__init__(message)


def action_hash(action):
    return hashlib.sha256(json.dumps(action, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SecOpsService:
    def __init__(self, store, step_delay=0.22):
        self.store, self.step_delay = store, step_delay

    def seed(self):
        if not self.store.list():
            for scenario in reversed(SCENARIOS):
                self.ingest(scenario, "system")

    def get(self, incident_id):
        item = self.store.get(incident_id)
        if not item:
            raise DomainError("事件不存在", 404)
        return item

    def ingest(self, scenario, actor):
        if scenario not in SCENARIOS:
            raise DomainError("未知演示场景", 422)
        if len(self.store.list()) >= 200:
            raise DomainError("演示事件已达200条，请使用已有事件", 422)
        return self.store.add(make_incident(scenario), actor)

    def _guard(self, item, actor, version, roles, statuses=None):
        if actor not in roles:
            raise DomainError("当前演示角色无权执行此操作", 403)
        if version != item["version"]:
            raise DomainError("数据已更新，请刷新后重试", 409)
        if statuses and item["status"] not in statuses:
            raise DomainError("当前状态不能执行此操作", 409)

    def start_investigation(self, incident_id, actor, version):
        def mutate(item):
            self._guard(item, actor, version, {"analyst", "manager"}, {"new", "needs_attention", "pending_review", "awaiting_verification"})
            if item["status"] == "awaiting_verification":
                expired = bool(item["execution"] and datetime.fromisoformat(item["execution"]["expires_at"]) <= datetime.now(timezone.utc))
                failed = bool(item["verification"] and not item["verification"]["passed"])
                if not expired and not failed:
                    raise DomainError("当前措施仍有效，请先进行处置回查")
            if item["report"] or item["trace"]:
                history = {key: item.get(key) for key in ["report", "evidence", "trace", "review", "action", "approval", "execution", "verification"]}
                history["archived_at"] = now()
                item.setdefault("history", []).append(history)
            item.update(status="investigating", evidence=[], trace=[], report=None, review=None, action=None, approval=None, execution=None, verification=None, error=None)
        return self.store.update(incident_id, mutate, actor, "investigate", "开始 LangGraph 模拟工具调查")

    def run_investigation(self, incident_id):
        try:
            case = self.get(incident_id)
            def emit(evidence, trace):
                def mutate(item):
                    if item["status"] != "investigating":
                        raise DomainError("调查任务状态已改变")
                    item["evidence"].append(evidence)
                    item["trace"].append(trace)
                self.store.update(incident_id, mutate)
            result = run_graph(case, emit, self.step_delay)
            def finish(item):
                if item["status"] != "investigating":
                    raise DomainError("调查任务状态已改变")
                item.update(report=result["report"], status="pending_review")
            self.store.update(incident_id, finish, "system", "report", "调查完成，等待分析员复核")
        except Exception:
            def fail(item):
                if item["status"] != "investigating":
                    return False
                item.update(status="needs_attention", error="调查未完成，已保留收集到的证据，请重试或人工接管。")
            self.store.update(incident_id, fail, "system", "investigation_error", "调查异常，转人工处理")

    def review(self, incident_id, actor, version, verdict, comment):
        if verdict not in {"malicious", "authorized", "false_positive", "inconclusive"} or len(comment.strip()) < 4:
            raise DomainError("请选择有效结论，并填写至少4个字的复核说明", 422)
        def mutate(item):
            self._guard(item, actor, version, {"analyst", "manager"}, {"pending_review"})
            if not item["report"]:
                raise DomainError("尚未生成调查报告")
            if item["report"]["missing"] and verdict in {"authorized", "false_positive"}:
                raise DomainError("证据不完整，不能确认授权或误报，请补充证据")
            if verdict == "authorized":
                authorization = next((e["data"] for e in item["evidence"] if e["tool"] == "authorization"), {})
                if not all(authorization.get(k) for k in ["authorized", "scope_matches", "within_window"]):
                    raise DomainError("没有匹配范围与时间的授权证据")
            item["review"] = {"actor": actor, "verdict": verdict, "comment": comment.strip(), "at": now()}
            item["approval"] = None
            ticket_id = item["ticket"]["id"] if item["ticket"] else "WO-" + item["id"][4:]
            item["ticket"] = {"id": ticket_id, "owner": item["owner"], "status": "open", "created_at": now(), "title": "调查处置：" + item["name"]}
            if verdict == "malicious":
                kind = "freeze_account" if item["category"] == "identity" else "block_ip"
                item["action"] = {"kind": kind, "target": item.get("account") if kind == "freeze_account" else item["ip"], "scope": item["asset"], "duration_minutes": 30, "simulated": True}
                item["status"] = "awaiting_approval"
            elif verdict == "inconclusive":
                item.update(status="needs_attention", action=None, error="待补充证据；相关调查工单保持打开。")
            else:
                item.update(status="awaiting_verification", action=None)
        return self.store.update(incident_id, mutate, actor, "review", comment)

    def approve(self, incident_id, actor, version, approved, comment):
        def mutate(item):
            self._guard(item, actor, version, {"manager"}, {"awaiting_approval"})
            if approved:
                item["approval"] = {"actor": actor, "approved": True, "at": now(), "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(), "action_hash": action_hash(item["action"]), "comment": comment}
                item["status"] = "ready_to_execute"
            else:
                item["approval"] = {"actor": actor, "approved": False, "at": now(), "comment": comment}
                item["status"] = "pending_review"
        return self.store.update(incident_id, mutate, actor, "approve" if approved else "reject", comment)

    def reopen_review(self, incident_id, actor, version):
        def mutate(item):
            self._guard(item, actor, version, {"analyst", "manager"}, {"ready_to_execute"})
            if item["execution"]:
                raise DomainError("已执行的措施不能退回未执行状态")
            item.update(status="pending_review", approval=None)
        return self.store.update(incident_id, mutate, actor, "reopen_review", "撤销当前审批并重新复核")

    def execute(self, incident_id, actor, version):
        def mutate(item):
            self._guard(item, actor, version, {"operator"})
            if item["execution"]:
                return False
            if item["status"] != "ready_to_execute" or not item["approval"] or not item["approval"]["approved"]:
                raise DomainError("缺少有效审批，不能执行")
            if item["approval"]["action_hash"] != action_hash(item["action"]):
                raise DomainError("操作参数发生变更，必须重新审批")
            if datetime.fromisoformat(item["approval"]["expires_at"]) <= datetime.now(timezone.utc):
                raise DomainError("审批已过期，需要重新复核审批")
            item["execution"] = {"id": "EX-" + uuid.uuid4().hex[:10], "actor": actor, "at": now(), "simulated": True, "applied": True, "action_hash": action_hash(item["action"]), "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=item["action"]["duration_minutes"])).isoformat(), "message": "模拟连接器已应用措施，未调用真实防火墙或账号系统。"}
            item["status"] = "awaiting_verification"
            item["ticket"]["status"] = "in_progress"
        return self.store.update(incident_id, mutate, actor, "execute", "执行已审批的模拟处置，未修改真实系统")

    def verify(self, incident_id, actor, version):
        def mutate(item):
            self._guard(item, actor, version, {"analyst", "manager"}, {"awaiting_verification"})
            authorized = item["review"]["verdict"] in {"authorized", "false_positive"}
            applied = bool(item["execution"] and item["execution"]["applied"] and datetime.fromisoformat(item["execution"]["expires_at"]) > datetime.now(timezone.utc))
            passed = (authorized or applied) and not item["fixture"].get("residual_risk", False) and not item["report"]["missing"]
            item["verification"] = {"actor": actor, "at": now(), "passed": passed, "simulated": True, "checks": ["复核授权或模拟措施状态", "核对工单与目标资产", "检查后续模拟日志与证据完整性"], "message": "模拟回查通过，分析员确认关闭。" if passed else "回查未通过，仍存在证据缺口或残留风险，事件保持打开。"}
            if passed:
                item["status"] = "resolved"
                item["ticket"]["status"] = "closed"
        return self.store.update(incident_id, mutate, actor, "verify", "执行模拟处置回查与人工关闭检查")

    def recover_interrupted(self):
        for case in self.store.list():
            if case["status"] == "investigating":
                def recover(item):
                    item.update(status="needs_attention", error="服务曾中断，已保留证据，请重新调查或人工接管。")
                self.store.update(case["id"], recover, "system", "recover", "恢复未完成任务，转人工可见状态")
