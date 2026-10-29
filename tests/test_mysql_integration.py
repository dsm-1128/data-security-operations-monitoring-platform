import os
import time

import pytest
from sqlalchemy import select

from app.platform.auth import demo_principal
from app.platform.database import Database, runs
from app.platform.runs import RunStore, Conflict
from app.seed import make_incident


@pytest.mark.skipif(not os.getenv('SECOPS_TEST_MYSQL_URL'), reason='Dedicated MySQL integration database not configured')
def test_mysql_timestamp_precision_and_expired_lease():
    db = Database(os.environ['SECOPS_TEST_MYSQL_URL'], initialize=True)
    store = RunStore(db)
    principal = demo_principal('analyst')
    run = store.create(make_incident('web_attack'), principal, 'simulated')
    with db.engine.connect() as connection:
        created = connection.execute(select(runs.c.created).where(runs.c.id == run['id'])).scalar_one()
    assert abs(created - run['created']) < 0.001
    old = store.claim(run['id'], 'worker-a', ttl=0.02)
    state = store.get(run['id'], principal)
    time.sleep(0.05)
    store.claim(run['id'], 'worker-b')
    with pytest.raises(Conflict):
        store.save(state, old)
    store.cancel(run['id'], principal)
    db.close()
