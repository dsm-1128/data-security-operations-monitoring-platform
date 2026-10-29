from sqlalchemy import JSON, Column, Float, Integer, MetaData, String, Table, Text, create_engine, event
from sqlalchemy.pool import StaticPool


metadata = MetaData()
runs = Table('platform_runs', metadata,
    Column('id', String(64), primary_key=True), Column('org', String(100), nullable=False, index=True),
    Column('asset', String(200), nullable=False), Column('owner', String(200), nullable=False),
    Column('status', String(40), nullable=False, index=True), Column('state', JSON, nullable=False),
    Column('version', Integer, nullable=False, default=0), Column('lease_owner', String(100), nullable=False, default=''),
    Column('fence', Integer, nullable=False, default=0), Column('lease_until', Float(53), nullable=False, default=0),
    Column('created', Float(53), nullable=False), Column('updated', Float(53), nullable=False))
events = Table('platform_events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True), Column('run_id', String(64), nullable=False, index=True),
    Column('kind', String(60), nullable=False), Column('payload', JSON, nullable=False), Column('at', Float(53), nullable=False))
audit = Table('platform_audit', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True), Column('org', String(100), nullable=False),
    Column('asset', String(200), nullable=False), Column('subject', String(200), nullable=False),
    Column('action', String(60), nullable=False), Column('target', String(64), nullable=False),
    Column('detail', JSON, nullable=False), Column('at', Float(53), nullable=False))
outbox = Table('platform_outbox', metadata,
    Column('id', String(64), primary_key=True), Column('run_id', String(64), nullable=False, index=True),
    Column('published', Float(53), nullable=True), Column('created', Float(53), nullable=False))
actions = Table('platform_actions', metadata,
    Column('id', String(64), primary_key=True), Column('run_id', String(64), nullable=False, index=True),
    Column('org', String(100), nullable=False), Column('asset', String(200), nullable=False),
    Column('status', String(40), nullable=False), Column('document', JSON, nullable=False),
    Column('version', Integer, nullable=False), Column('created', Float(53), nullable=False))
sessions = Table('platform_sessions', metadata,
    Column('token_hash', String(64), primary_key=True), Column('subject', String(200), nullable=False),
    Column('expires', Float(53), nullable=False), Column('kind', String(30), nullable=False), Column('document', JSON, nullable=False))


class Database:
    def __init__(self, url, initialize=False):
        self.url = url
        options = {'pool_pre_ping': True}
        if url.startswith('sqlite'):
            options['connect_args'] = {'check_same_thread': False, 'timeout': 30}
            if ':memory:' in url:
                options['poolclass'] = StaticPool
        self.engine = create_engine(url, **options)
        if url.startswith('sqlite'):
            @event.listens_for(self.engine, 'connect')
            def configure(connection, _):
                connection.execute('PRAGMA journal_mode=WAL')
        if initialize:
            metadata.create_all(self.engine)

    def close(self):
        self.engine.dispose()
