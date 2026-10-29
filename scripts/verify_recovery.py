"""Crash a synthetic investigation after a persisted observation or handoff.

Run in the API container with PYTHONPATH=/app. The probe expires only its own
test run's lease to avoid waiting 120 seconds; production uses real lease expiry.
SECOPS_RECOVERY_PHASE=handoff additionally probes a multi-agent handoff.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import update
from app.platform.auth import demo_principal
from app.platform.config import Settings, config_fingerprint
from app.platform.database import Database, runs
from app.platform.runs import RunStore
from app.platform.runtime import process_run
from app.seed import make_incident

settings = Settings.from_env()
assert settings.mode == 'development' and settings.model_mode == settings.connector_mode == 'simulated'
phase = os.getenv('SECOPS_RECOVERY_PHASE', 'observation')
assert phase in {'observation', 'handoff'}
assert phase != 'handoff' or settings.agent_arch == 'multi'
store = RunStore(Database(settings.database_url))
principal = demo_principal('analyst')

if len(sys.argv) > 1 and sys.argv[1].startswith('RUN-'):
    original_save = store.save
    def crash_after_observation(snapshot, lease):
        original_save(snapshot, lease)
        if (phase == 'observation' and snapshot['evidence']
                or phase == 'handoff' and snapshot['trace'] and snapshot['trace'][-1]['kind'] == 'subagent_end'):
            os._exit(17)
    store.save = crash_after_observation
    process_run(store, settings, sys.argv[1])
    raise SystemExit('Expected process crash did not occur')

probe = Path('/tmp/secops-recovery-probe-' + phase + '.json')
if sys.argv[1:] == ['--prepare']:
    # Stop this project's dispatcher before prepare, then restart before finish.
    run = store.create(make_incident('account_anomaly'), principal, 'simulated', config_fingerprint=config_fingerprint(settings))
    result = subprocess.run([sys.executable, __file__, run['id']], check=False)
    assert result.returncode == 17, result.returncode
    interrupted = store.get(run['id'], principal)
    assert interrupted['status'] == 'running' and len(interrupted['evidence']) == (1 if phase == 'observation' else 2)
    probe.write_text(json.dumps({'id': run['id'], 'evidence_id': interrupted['evidence'][0]['id']}))
    print(json.dumps({'crashed_after_checkpoint': True, 'phase': phase, 'run_id': run['id']}))
    raise SystemExit(0)
assert sys.argv[1:] == ['--finish'], 'Use --prepare with dispatcher stopped, then --finish with dispatcher started'
run = json.loads(probe.read_text())
first_evidence_id = run['evidence_id']
with store.db.engine.begin() as connection:
    connection.execute(update(runs).where(runs.c.id == run['id']).values(lease_until=time.time() - 1))
started = time.monotonic()
while True:
    recovered = store.get(run['id'], principal)
    if recovered['status'] not in {'queued', 'running'}:
        break
    assert time.monotonic() - started < 30, 'Recovery dispatcher failed'
    time.sleep(0.25)
assert recovered['status'] == 'waiting_review', recovered['reason']
assert recovered['evidence'][0]['id'] == first_evidence_id
assert len([e for e in recovered['evidence'] if e['tool'] == 'asset_context']) == 1
print(json.dumps({'process_crash_recovery': 'passed', 'saved_observation_reused': True,
    'phase': phase, 'lease_expiry': 'accelerated only for test run', 'run_id': run['id']}))
