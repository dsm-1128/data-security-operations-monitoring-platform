"""SQLite transactions protect state updates and their audit records together."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from .seed import now


class MissingIncidentError(LookupError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, incident_id TEXT, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, role TEXT NOT NULL, expires REAL NOT NULL);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT document FROM incidents ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, incident_id):
        with self.connect() as connection:
            row = connection.execute("SELECT document FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def add(self, incident, actor="system"):
        with self.lock, self.connect() as connection:
            connection.execute("INSERT INTO incidents VALUES (?,?)", (incident["id"], json.dumps(incident, ensure_ascii=False)))
            self._audit(connection, actor, "ingest", incident["id"], "接入模拟告警，保留原始分组数量")
        return incident

    def update(self, incident_id, fn, actor=None, action=None, detail=""):
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT document FROM incidents WHERE id=?", (incident_id,)).fetchone()
            if not row:
                raise MissingIncidentError(incident_id)
            item = json.loads(row[0])
            changed = fn(item)
            if changed is False:
                return item
            item["version"] += 1
            item["updated_at"] = now()
            connection.execute("UPDATE incidents SET document=? WHERE id=?", (json.dumps(item, ensure_ascii=False), incident_id))
            if action:
                self._audit(connection, actor or "system", action, incident_id, detail)
        return item

    def _audit(self, connection, actor, action, incident_id, detail):
        connection.execute("INSERT INTO audit(at,actor,action,incident_id,detail) VALUES (?,?,?,?,?)", (now(), actor, action, incident_id, detail))

    def audit(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 500").fetchall()
        return [dict(row) for row in rows]
