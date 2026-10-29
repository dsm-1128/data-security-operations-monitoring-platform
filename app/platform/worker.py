import os
import time

from celery import Celery
from celery.signals import heartbeat_sent, worker_ready
import redis

from .config import Settings
from .database import Database
from .runs import RunStore
from .runtime import process_run


settings = Settings.from_env()
celery_app = Celery('secops', broker=settings.redis_url)
celery_app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    task_serializer='json', accept_content=['json'], result_serializer='json', task_ignore_result=True,
    broker_transport_options={'visibility_timeout': 600}, task_soft_time_limit=330, task_time_limit=360,
    broker_connection_retry_on_startup=True)


@celery_app.task(name='secops.investigate')
def investigate(run_id):
    settings.validate_startup()
    db = Database(settings.database_url)
    try:
        process_run(RunStore(db), settings, run_id)
    finally:
        db.close()


@heartbeat_sent.connect
@worker_ready.connect
def heartbeat(**kwargs):
    try:
        redis.Redis.from_url(settings.redis_url, socket_timeout=2).set('secops:worker:heartbeat', str(time.time()), ex=60)
    except redis.RedisError:
        pass
