"""Development-only MySQL backup/isolated restore probe; briefly stops app services.

Run from the repository root with Python 3.12. Existing backups are never overwritten.
Credentials are expanded only within the database container and never printed.
"""
import json
from pathlib import Path
import re
import subprocess
import uuid


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ['docker', 'compose', '--env-file', '.env.platform', '-f', 'compose.platform.yaml']


def run(arguments, **kwargs):
    return subprocess.run(COMPOSE + arguments, cwd=ROOT, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs).stdout


def sql(statement):
    return run(['exec', '-T', 'mysql', 'sh', '-c',
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root --batch --skip-column-names'], input=statement.encode()).decode().strip()


def counts(database, tables):
    return {table: int(sql(f'SELECT COUNT(*) FROM `{database}`.`{table}`;')) for table in tables}


def main():
    modes = json.loads(run(['exec', '-T', 'api', 'python', '-c',
        "import json; from app.platform.config import Settings; s=Settings.from_env(); print(json.dumps([s.mode,s.model_mode,s.connector_mode]))"]))
    if modes != ['development', 'simulated', 'simulated']:
        raise SystemExit('Backup probe is limited to simulated development configuration')
    probe = 'secops_restore_probe_' + uuid.uuid4().hex[:12]
    backup = ROOT / '.backups' / (probe + '.sql')
    backup.parent.mkdir(exist_ok=True)
    created = False
    try:
        run(['stop', 'api', 'worker', 'dispatcher'])
        tables = sql('SHOW TABLES FROM secops;').splitlines()
        assert tables and all(re.fullmatch(r'[a-zA-Z0-9_]+', table) for table in tables)
        original = counts('secops', tables)
        payload = run(['exec', '-T', 'mysql', 'sh', '-c',
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -u root --single-transaction --no-tablespaces --set-gtid-purged=OFF secops'])
        with backup.open('xb') as stream:
            stream.write(payload)
        sql(f'CREATE DATABASE `{probe}` CHARACTER SET utf8mb4;')
        created = True
        run(['exec', '-T', 'mysql', 'sh', '-c',
             f'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root {probe}'], input=payload)
        restored = counts(probe, tables)
        assert restored == original, 'Restored row counts differ'
        assert sql(f'SELECT version_num FROM `{probe}`.alembic_version;') == sql('SELECT version_num FROM secops.alembic_version;')
        print(json.dumps({'mysql_backup_restore': 'passed', 'tables_verified': len(tables),
                          'rows_verified': sum(restored.values()), 'backup_file': str(backup),
                          'scope': 'logical database restore only; evidence storage backup not included'}, ensure_ascii=False))
    finally:
        try:
            if created:
                sql(f'DROP DATABASE `{probe}`;')
        finally:
            run(['start', 'api', 'worker', 'dispatcher'])


if __name__ == '__main__':
    main()
