import copy
import time

import pytest

from app.platform.database import Database
from app.platform.runs import RunStore, Conflict
from app.platform.auth import Principal
from app.seed import make_incident


@pytest.fixture
def store(tmp_path):
    return RunStore(Database('sqlite:///' + str(tmp_path / 'platform.db'), initialize=True))


def actor(subject='analyst', assets=('*',), org='demo'):
    return Principal(subject, org, ('analyst',), assets)


def test_run_survives_restart_and_events_resume(store):
    run = store.create(make_incident('web_attack'), actor(), 'simulated')
    lease = store.claim(run['id'], 'worker-a')
    state = store.get(run['id'], actor())
    state['rounds'] = 1
    state['messages'].append({'role': 'user', 'content': 'saved'})
    store.save(state, lease)
    reopened = RunStore(Database(store.db.url))
    assert reopened.get(run['id'], actor())['rounds'] == 1
    events = reopened.events(run['id'], actor(), after=0)
    assert reopened.events(run['id'], actor(), after=events[-1]['id']) == []


def test_expired_worker_cannot_commit(store):
    run = store.create(make_incident('web_attack'), actor(), 'simulated')
    old = store.claim(run['id'], 'old', ttl=0.01)
    state = store.get(run['id'], actor())
    time.sleep(0.03)
    new = store.claim(run['id'], 'new')
    assert new != old
    with pytest.raises(Conflict):
        store.save(state, old)


def test_duplicate_delivery_has_one_owner(store):
    run = store.create(make_incident('web_attack'), actor(), 'simulated')
    store.claim(run['id'], 'first')
    with pytest.raises(Conflict):
        store.claim(run['id'], 'second')
    assert len(store.pending_outbox()) == 1


def test_cross_scope_cannot_read_events_or_cancel(store):
    run = store.create(make_incident('web_attack'), actor(), 'simulated')
    for principal in [actor(assets=('unrelated',)), actor(org='other')]:
        with pytest.raises(PermissionError):
            store.get(run['id'], principal)
        with pytest.raises(PermissionError):
            store.events(run['id'], principal)
        with pytest.raises(PermissionError):
            store.cancel(run['id'], principal)


def test_cancel_fences_running_worker(store):
    run = store.create(make_incident('web_attack'), actor(), 'simulated')
    lease = store.claim(run['id'], 'worker')
    state = store.get(run['id'], actor())
    store.cancel(run['id'], actor())
    with pytest.raises(Conflict):
        store.save(state, lease)
    assert store.get(run['id'], actor())['status'] == 'cancelled'


def test_create_outside_asset_scope_fails(store):
    with pytest.raises(PermissionError):
        store.create(make_incident('web_attack'), actor(assets=('elsewhere',)), 'simulated')
