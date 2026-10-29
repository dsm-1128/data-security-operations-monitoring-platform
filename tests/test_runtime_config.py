from app.platform.auth import demo_principal
from app.platform.config import Settings, config_fingerprint
from app.platform.database import Database
from app.platform.runs import RunStore
from app.platform.runtime import process_run
from app.seed import make_incident


def test_changed_configuration_is_not_mixed_into_saved_run(tmp_path):
    settings = Settings()
    store = RunStore(Database('sqlite:///' + str(tmp_path / 'runs.db'), initialize=True))
    run = store.create(make_incident('web_attack'), demo_principal('analyst'), 'simulated', config_fingerprint=config_fingerprint(settings))
    settings.connector_version = 'v2'
    process_run(store, settings, run['id'])
    result = store.get(run['id'], demo_principal('analyst'))
    assert result['status'] == 'needs_attention'
    assert result['rounds'] == 0
    assert result['evidence'] == []
