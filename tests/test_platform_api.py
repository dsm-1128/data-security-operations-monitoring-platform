import pytest
from fastapi.testclient import TestClient

from app.platform.api import create_platform_app
from app.platform.config import Settings


@pytest.fixture(params=['single', 'multi'])
def client(tmp_path, request):
    settings = Settings(agent_arch=request.param, database_url='sqlite:///' + str(tmp_path / 'api.db'))
    with TestClient(create_platform_app(settings)) as client:
        yield client


def login(client, role='analyst'):
    assert client.post('/api/platform/session/demo', json={'role': role}).status_code == 200


def test_api_requires_identity_and_blocks_cross_origin(client):
    assert client.get('/api/platform/runs').status_code == 401
    assert client.post('/api/platform/session/demo', json={'role': 'manager'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/healthz').status_code == 200


def test_run_trace_review_and_approval_journey(client):
    login(client)
    response = client.post('/api/platform/runs', json={'scenario': 'web_attack'})
    assert response.status_code == 201
    run_id = response.json()['id']
    run = client.get('/api/platform/runs/' + run_id).json()
    assert run['status'] == 'waiting_review'
    assert run['mode'] == 'simulated'
    assert len(run['trace']) > 5
    assert 'fixture' not in run['case']
    events = client.get(f'/api/platform/runs/{run_id}/events').json()
    assert client.get(f'/api/platform/runs/{run_id}/events?after={events[-1]["id"]}').json() == []
    assert client.post(f'/api/platform/runs/{run_id}/review', json={'verdict': 'malicious', 'comment': '人工核对了原始证据'}).status_code == 200
    action = client.post(f'/api/platform/runs/{run_id}/actions', json={'kind': 'block_ip', 'target': run['case']['ip'], 'duration_minutes': 30, 'reason': '限制恶意请求来源'}).json()
    login(client, 'manager')
    assert client.post(f'/api/platform/actions/{action["id"]}/approve', json={'approved': True, 'comment': '同意'}).status_code == 200
    login(client, 'operator')
    assert client.post(f'/api/platform/actions/{action["id"]}/execute').json()['status'] == 'applied'
    login(client)
    assert client.post(f'/api/platform/actions/{action["id"]}/verify', json={'comment': '核对外部状态与业务影响'}).status_code == 200
    assert client.get('/api/platform/runs/' + run_id).json()['status'] == 'completed'


def test_operator_cannot_create_run(client):
    login(client, 'operator')
    assert client.post('/api/platform/runs', json={'scenario': 'web_attack'}).status_code == 403


def test_missing_evidence_is_visible_handoff(client):
    login(client)
    run = client.post('/api/platform/runs', json={'scenario': 'missing_logs'}).json()
    result = client.get('/api/platform/runs/' + run['id']).json()
    assert result['status'] == 'needs_attention'
    assert result['report'] is None


def test_logout_revokes_session(client):
    login(client)
    cookies = dict(client.cookies)
    assert client.post('/api/platform/logout').status_code == 200
    client.cookies.update(cookies)
    assert client.get('/api/platform/me').status_code == 401


def test_archived_evidence_download_and_persistent_metrics(client):
    login(client)
    run_id = client.post('/api/platform/runs', json={'scenario': 'web_attack'}).json()['id']
    run = client.get('/api/platform/runs/' + run_id).json()
    evidence = run['evidence'][0]
    response = client.get(f'/api/platform/runs/{run_id}/evidence/{evidence["id"]}')
    assert response.status_code == 200
    assert response.json()['content_hash'] == evidence['content_hash']
    assert client.get(f'/api/platform/runs/{run_id}/evidence/EV-' + '0' * 32).status_code == 404
    assert client.get('/metrics').status_code == 403
    login(client, 'auditor')
    response = client.get('/metrics')
    assert response.status_code == 200
    assert 'secops_persisted_runs{mode="simulated",status="waiting_review"} 1.0' in response.text
