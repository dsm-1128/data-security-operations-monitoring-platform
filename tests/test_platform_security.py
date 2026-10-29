import time

import httpx
import pytest

from app.platform.auth import Principal, authorize
from app.platform.config import Settings
from app.platform.database import Database
from app.platform.runs import RunStore, Conflict
from app.platform.execution import ActionService
from app.platform.connectors import GatewayConnector
from app.seed import make_incident


def person(name, role):
    return Principal(name, 'demo', (role,), ('*',))


class Connector:
    version = 'v1'
    simulated = True
    def __init__(self):
        self.calls = []
    def execute(self, operation_id, action):
        self.calls.append(operation_id)
        return {'status': 'applied', 'external_id': operation_id}
    def query(self, operation_id):
        return {'status': 'applied', 'external_id': operation_id}


@pytest.fixture
def setup_action(tmp_path):
    store = RunStore(Database('sqlite:///' + str(tmp_path / 'p.db'), initialize=True))
    analyst = person('alice', 'analyst')
    run = store.create(make_incident('web_attack'), analyst, 'simulated')
    lease = store.claim(run['id'], 'worker')
    state = store.get(run['id'], analyst)
    state.update(status='waiting_review', report={'verdict': 'malicious', 'missing': []})
    store.save(state, lease)
    store.review(run['id'], analyst, 'malicious', '已核验原始证据')
    connector = Connector()
    service = ActionService(store, connector)
    action = service.request(run['id'], analyst, 'block_ip', run['case']['ip'], 30, '限制已确认恶意来源')
    return service, connector, action


def test_production_configuration_fails_closed():
    with pytest.raises(ValueError):
        Settings(mode='production').validate_startup()


@pytest.mark.parametrize('limits', [{'max_rounds': 0}, {'max_tools': 200}, {'max_seconds': 400}, {'max_tokens': -1}])
def test_budget_configuration_stays_within_worker_safety_limits(limits):
    with pytest.raises(ValueError):
        Settings(**limits).validate_startup()


def test_self_approval_rejected(setup_action):
    service, _, action = setup_action
    with pytest.raises(PermissionError):
        service.approve(action['id'], person('alice', 'manager'), True, '批准')


def test_execution_is_idempotent_and_bound_to_connector_version(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准')
    connector.version = 'v2'
    with pytest.raises(Conflict):
        service.execute(action['id'], person('carol', 'operator'))
    assert connector.calls == []
    connector.version = 'v1'
    first = service.execute(action['id'], person('carol', 'operator'))
    second = service.execute(action['id'], person('carol', 'operator'))
    assert first['status'] == second['status'] == 'applied'
    assert connector.calls == [action['id']]


def test_timeout_is_unknown_not_retried(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准')
    def fail(operation_id, parameters):
        connector.calls.append(operation_id)
        raise TimeoutError()
    connector.execute = fail
    result = service.execute(action['id'], person('carol', 'operator'))
    assert result['status'] == 'execution_unknown'
    with pytest.raises(Conflict):
        service.execute(action['id'], person('carol', 'operator'))
    assert connector.calls == [action['id']]
    assert service.reconcile(action['id'], person('carol', 'operator'))['status'] == 'applied'


def test_expired_approval_does_not_execute(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准', ttl=-1)
    with pytest.raises(Conflict):
        service.execute(action['id'], person('carol', 'operator'))
    assert connector.calls == []


def test_expired_approval_can_be_withdrawn_and_replaced(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准', ttl=-1)
    analyst = person('alice', 'analyst')
    withdrawn = service.withdraw(action['id'], analyst, '审批已过期，重新核对参数')
    assert withdrawn['status'] == 'withdrawn'
    replacement = service.request(action['run_id'], analyst, 'block_ip', action['parameters']['target'], 15, '更新处置范围')
    assert replacement['id'] != action['id']
    with pytest.raises(Conflict):
        service.execute(action['id'], person('carol', 'operator'))
    assert connector.calls == []


def test_only_requester_or_manager_can_withdraw_unexecuted_action(setup_action):
    service, connector, action = setup_action
    with pytest.raises(PermissionError):
        service.withdraw(action['id'], person('other', 'analyst'), '无权撤回别人申请')
    assert service.withdraw(action['id'], person('bob', 'manager'), '变更连接器，需要重新申请')['status'] == 'withdrawn'


def test_unknown_execution_cannot_be_withdrawn_for_replay(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准')
    connector.execute = lambda *_: {'status': 'unknown'}
    service.execute(action['id'], person('carol', 'operator'))
    with pytest.raises(Conflict):
        service.withdraw(action['id'], person('bob', 'manager'), '不得重放未知外部操作')


def test_http_connector_sends_fixed_scope_and_limits_response():
    requests = []
    def remote(request):
        requests.append(request)
        return httpx.Response(200, json={'complete': True, 'data': {'records': [], 'token': 'secret'}, 'summary': 'none'})
    gateway = GatewayConnector('https://approved.example', 'key', transport=httpx.MockTransport(remote))
    result = gateway.read('security_logs', {'asset': 'orders', 'limit': 10}, {})
    assert requests[0].url.path == '/queries/security_logs'
    assert result['data']['token'] == '[REDACTED]'
    with pytest.raises(PermissionError):
        gateway.read('../admin', {}, {})


def test_unknown_roles_never_grant_access():
    with pytest.raises(PermissionError):
        authorize(person('attacker', 'root'), 'orders', 'approve')


def test_verification_failure_does_not_partially_mark_action_verified(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准')
    service.execute(action['id'], person('carol', 'operator'))
    service.store.transition(action['run_id'], person('alice', 'analyst'), 'review', {'reviewed'}, 'needs_attention', '需要重新核查')
    with pytest.raises(Conflict):
        service.verify(action['id'], person('alice', 'analyst'), '核对业务影响完成')
    assert service.get(action['id'], person('alice', 'analyst'))['status'] == 'applied'


def test_closed_or_changed_investigation_cannot_execute_old_approval(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准')
    service.store.transition(action['run_id'], person('alice', 'analyst'), 'review', {'reviewed'}, 'completed', '事件关闭')
    with pytest.raises(Conflict):
        service.execute(action['id'], person('carol', 'operator'))
    assert connector.calls == []


@pytest.mark.parametrize('operation', ['reconcile', 'verify'])
def test_reconciliation_rejects_changed_connector(setup_action, operation):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准')
    service.execute(action['id'], person('carol', 'operator'))
    connector.version = 'v2'
    with pytest.raises(Conflict):
        if operation == 'verify':
            service.verify(action['id'], person('alice', 'analyst'), '核验原始证据')
        else:
            service.reconcile(action['id'], person('carol', 'operator'))


def test_same_version_different_gateway_cannot_reconcile(setup_action):
    service, connector, action = setup_action
    service.approve(action['id'], person('bob', 'manager'), True, '批准')
    service.execute(action['id'], person('carol', 'operator'))
    connector.base_url = 'https://different.example'
    with pytest.raises(Conflict):
        service.reconcile(action['id'], person('carol', 'operator'))
