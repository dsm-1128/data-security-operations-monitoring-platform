"""Persisted gauges work across API and Celery processes; traces contain no prompts."""
import json
import os
import time

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from sqlalchemy import func, select

from .database import outbox, runs


_providers = {}


def tracer():
    pid = os.getpid()
    if pid not in _providers:
        provider = TracerProvider(resource=Resource.create({'service.name': 'secops-agent-worker'}))
        if os.getenv('SECOPS_TRACE_CONSOLE', '0') == '1':
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(
                formatter=lambda span: json.dumps(json.loads(span.to_json()), ensure_ascii=False) + '\n')))
        _providers[pid] = provider
    return _providers[pid].get_tracer('secops.platform')


def persisted_metrics(db, principal):
    registry = CollectorRegistry()
    count = Gauge('secops_persisted_runs', 'Persisted runs visible to this identity', ['mode', 'status'], registry=registry)
    pending = Gauge('secops_outbox_pending', 'Unpublished visible investigation messages', registry=registry)
    age = Gauge('secops_oldest_active_seconds', 'Age since last update of oldest active visible run', registry=registry)
    condition = runs.c.org == principal.org
    if '*' not in principal.assets:
        condition &= runs.c.asset.in_(principal.assets)
    mode = runs.c.state['mode'].as_string()
    with db.engine.connect() as connection:
        for row in connection.execute(select(mode, runs.c.status, func.count()).where(condition).group_by(mode, runs.c.status)):
            count.labels(row[0], row[1]).set(row[2])
        pending.set(connection.execute(select(func.count()).select_from(outbox.join(runs, runs.c.id == outbox.c.run_id))
            .where(condition, outbox.c.published.is_(None))).scalar_one())
        oldest = connection.execute(select(func.min(runs.c.updated)).where(condition, runs.c.status.in_(['queued', 'running']))).scalar_one()
        age.set(max(0, time.time() - oldest) if oldest else 0)
    return generate_latest(registry)
