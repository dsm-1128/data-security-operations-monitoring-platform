"""Container-side integration check using synthetic incidents only.

Run inside the API container with: python /tmp/verify_platform.py
This exercises actual MySQL, Redis, Outbox, Celery and HTTP API. It does not
claim validation of live model providers, enterprise SSO or real remediation.
"""
import json
import time

import httpx
from sqlalchemy import select

from app.platform.config import Settings
from app.platform.database import Database, runs
from app.platform.runs import RunStore, Conflict
from app.platform.auth import demo_principal
from app.seed import make_incident


settings = Settings.from_env()
if settings.mode != 'development' or settings.model_mode != 'simulated' or settings.connector_mode != 'simulated':
    raise SystemExit('Verification requires explicitly simulated development configuration')

store = RunStore(Database(settings.database_url))
principal = demo_principal('analyst')
sample = store.create(make_incident('web_attack'), principal, 'simulated')
with store.db.engine.connect() as connection:
    stored_time = connection.execute(select(runs.c.created).where(runs.c.id == sample['id'])).scalar_one()
assert abs(stored_time - sample['created']) < 0.001, 'MySQL timestamp precision'
lease = store.claim(sample['id'], 'precision-test', ttl=0.02)
snapshot = store.get(sample['id'], principal)
time.sleep(0.05)
store.claim(sample['id'], 'recovery-test')
try:
    store.save(snapshot, lease)
    raise AssertionError('Old worker was not fenced')
except Conflict:
    pass
store.cancel(sample['id'], principal)

with httpx.Client(base_url='http://127.0.0.1:8080', timeout=15, trust_env=False) as client:
    def post(path, body=None):
        response = client.post('/api/platform' + path, json=body or {})
        response.raise_for_status()
        return response.json()

    def get(path):
        response = client.get('/api/platform' + path)
        response.raise_for_status()
        return response.json()

    assert client.get('/readyz').status_code == 200
    post('/session/demo', {'role': 'analyst'})
    created = [post('/runs', {'scenario': name}) for name in ['web_attack', 'account_anomaly', 'authorized_scan', 'missing_logs', 'blocked_attack']]
    started = time.monotonic()
    while True:
        results = [get('/runs/' + row['id']) for row in created]
        if all(row['status'] not in {'queued', 'running'} for row in results):
            break
        assert time.monotonic() - started < 45, 'Independent Worker did not complete in time'
        time.sleep(0.25)
    assert [r['status'] for r in results] == ['waiting_review', 'waiting_review', 'waiting_review', 'needs_attention', 'waiting_review']
    run = results[0]
    run_id = run['id']
    evidence = run['evidence'][0]
    assert get(f'/runs/{run_id}/evidence/{evidence["id"]}')['content_hash'] == evidence['content_hash']
    post(f'/runs/{run_id}/review', {'verdict': 'malicious', 'comment': '集成测试核验模拟证据'})
    action = post(f'/runs/{run_id}/actions', {'kind': 'block_ip', 'target': run['case']['ip'], 'duration_minutes': 30, 'reason': '集成测试模拟处置'})
    assert post('/actions/' + action['id'] + '/withdraw', {'comment': '集成测试重新申请'})['status'] == 'withdrawn'
    action = post(f'/runs/{run_id}/actions', {'kind': 'block_ip', 'target': run['case']['ip'], 'duration_minutes': 30, 'reason': '集成测试模拟处置'})
    post('/session/demo', {'role': 'manager'})
    post('/actions/' + action['id'] + '/approve', {'approved': True, 'comment': '集成测试批准'})
    post('/session/demo', {'role': 'operator'})
    assert post('/actions/' + action['id'] + '/execute')['status'] == 'applied'
    assert post('/actions/' + action['id'] + '/execute')['status'] == 'applied'
    post('/session/demo', {'role': 'analyst'})
    post('/actions/' + action['id'] + '/verify', {'comment': '集成测试核验模拟外部状态'})
    assert get('/runs/' + run_id)['status'] == 'completed'
    events = get(f'/runs/{run_id}/events')
    assert get(f'/runs/{run_id}/events?after={events[-1]["id"]}') == []
    post('/session/demo', {'role': 'auditor'})
    metrics = client.get('/metrics')
    assert metrics.status_code == 200 and 'secops_persisted_runs{mode="simulated",status="completed"}' in metrics.text
    print(json.dumps({'mysql_precision': 'passed', 'lease_fencing': 'passed', 'celery_scenarios': len(results),
        'human_response_lifecycle': 'passed', 'event_resume': 'passed', 'evidence_export': 'passed',
        'withdrawal': 'passed', 'persistent_metrics': 'passed', 'run_id': run_id,
        'model': 'simulated', 'connectors': 'simulated'}, ensure_ascii=False))
