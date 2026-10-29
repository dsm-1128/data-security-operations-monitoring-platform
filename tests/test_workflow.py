import copy
from datetime import datetime, timedelta, timezone

import pytest

from app.service import SecOpsService, DomainError
from app.storage import Store


@pytest.fixture
def service(tmp_path):
    return SecOpsService(Store(tmp_path / "demo.sqlite3"), step_delay=0)


def investigated(service, scenario="web_attack"):
    case = service.ingest(scenario, "analyst")
    service.start_investigation(case["id"], "analyst", case["version"])
    service.run_investigation(case["id"])
    return service.get(case["id"])


def reviewed(service):
    case = investigated(service)
    return service.review(case["id"], "analyst", case["version"], "malicious", "已核验请求与应用日志，建议临时封禁来源。")


def test_lifecycle_is_persistent_and_execution_is_idempotent(service):
    case = reviewed(service)
    assert case["status"] == "awaiting_approval"
    approved = service.approve(case["id"], "manager", case["version"], True, "同意临时封禁")
    executed = service.execute(case["id"], "operator", approved["version"])
    repeated = service.execute(case["id"], "operator", executed["version"])
    assert repeated["execution"]["id"] == executed["execution"]["id"]
    assert len([a for a in service.store.audit() if a["action"] == "execute"]) == 1
    final = service.verify(case["id"], "analyst", repeated["version"])
    assert final["status"] == "resolved"
    assert SecOpsService(Store(service.store.path)).get(case["id"])["status"] == "resolved"


def test_missing_logs_stay_inconclusive(service):
    case = investigated(service, "missing_logs")
    assert case["report"]["verdict"] == "inconclusive"
    assert case["report"]["impact"] == "unknown"
    with pytest.raises(DomainError, match="证据"):
        service.review(case["id"], "analyst", case["version"], "false_positive", "没有查到异常所以关闭")


def test_blocked_attack_remains_malicious(service):
    case = investigated(service, "blocked_attack")
    assert case["report"]["verdict"] == "malicious"
    assert case["report"]["defense"] == "blocked"


def test_authorized_scanning_is_not_a_malicious_incident(service):
    case = investigated(service, "authorized_scan")
    assert case["report"]["verdict"] == "authorized"
    case = service.review(case["id"], "analyst", case["version"], "authorized", "已核对扫描范围与有效授权")
    assert case["status"] == "awaiting_verification"
    assert service.verify(case["id"], "analyst", case["version"])["status"] == "resolved"


def test_role_boundary_and_stale_versions(service):
    case = reviewed(service)
    with pytest.raises(DomainError) as denied:
        service.approve(case["id"], "analyst", case["version"], True, "自批")
    assert denied.value.code == 403
    with pytest.raises(DomainError) as stale:
        service.approve(case["id"], "manager", case["version"] - 1, True, "审批")
    assert stale.value.code == 409
    with pytest.raises(DomainError):
        service.execute(case["id"], "operator", case["version"])


def test_expired_approval_cannot_execute(service):
    case = reviewed(service)
    case = service.approve(case["id"], "manager", case["version"], True, "同意")
    def expire(item):
        item["approval"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    service.store.update(case["id"], expire)
    case = service.get(case["id"])
    with pytest.raises(DomainError, match="过期"):
        service.execute(case["id"], "operator", case["version"])


def test_changed_action_invalidates_approval(service):
    case = reviewed(service)
    case = service.approve(case["id"], "manager", case["version"], True, "同意")
    service.store.update(case["id"], lambda item: item["action"].update(target="198.51.100.99"))
    case = service.get(case["id"])
    with pytest.raises(DomainError, match="变更"):
        service.execute(case["id"], "operator", case["version"])


def test_failed_verification_does_not_close(service):
    case = reviewed(service)
    case = service.approve(case["id"], "manager", case["version"], True, "同意")
    case = service.execute(case["id"], "operator", case["version"])
    service.store.update(case["id"], lambda item: item["fixture"].update(residual_risk=True))
    case = service.get(case["id"])
    result = service.verify(case["id"], "analyst", case["version"])
    assert result["status"] == "awaiting_verification"
    assert result["verification"]["passed"] is False


def test_account_export_uses_account_specific_action(service):
    case = investigated(service, "account_anomaly")
    assert case["report"]["verdict"] == "malicious"
    assert case["report"]["impact"] == "confirmed"
    case = service.review(case["id"], "analyst", case["version"], "malicious", "已联系账号负责人核验异常导出")
    assert case["action"]["kind"] == "freeze_account"


def test_interrupted_investigation_is_visible_after_restart(service):
    case = service.ingest("web_attack", "analyst")
    service.start_investigation(case["id"], "analyst", case["version"])
    restarted = SecOpsService(Store(service.store.path))
    restarted.recover_interrupted()
    assert restarted.get(case["id"])["status"] == "needs_attention"


def test_expired_action_can_return_to_review_without_reusing_approval(service):
    case = reviewed(service)
    case = service.approve(case["id"], "manager", case["version"], True, "同意")
    case = service.reopen_review(case["id"], "analyst", case["version"])
    assert case["status"] == "pending_review"
    assert case["approval"] is None
    with pytest.raises(DomainError):
        service.execute(case["id"], "operator", case["version"])


def test_expired_execution_allows_new_investigation_and_preserves_history(service):
    case = reviewed(service)
    case = service.approve(case["id"], "manager", case["version"], True, "同意")
    case = service.execute(case["id"], "operator", case["version"])
    previous_execution = case["execution"]["id"]
    ticket_id = case["ticket"]["id"]
    service.store.update(case["id"], lambda item: item["execution"].update(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    case = service.get(case["id"])
    case = service.verify(case["id"], "analyst", case["version"])
    assert case["verification"]["passed"] is False
    service.start_investigation(case["id"], "analyst", case["version"])
    service.run_investigation(case["id"])
    case = service.get(case["id"])
    assert case["history"][-1]["execution"]["id"] == previous_execution
    assert case["execution"] is None
    case = service.review(case["id"], "analyst", case["version"], "malicious", "重新核验后申请新的临时措施")
    assert case["ticket"]["id"] == ticket_id
    case = service.approve(case["id"], "manager", case["version"], True, "再次批准")
    case = service.execute(case["id"], "operator", case["version"])
    assert case["execution"]["id"] != previous_execution
    assert service.verify(case["id"], "analyst", case["version"])["status"] == "resolved"


def test_store_closes_connections_after_transaction(service):
    import sqlite3
    with service.store.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
