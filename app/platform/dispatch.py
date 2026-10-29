import logging
import time

from .config import Settings
from .database import Database
from .runs import RunStore


def dispatch_pending(store, send):
    store.recover_expired()
    sent = 0
    for message in store.pending_outbox():
        send(message['run_id'])
        store.mark_published(message['id'])
        sent += 1
    return sent


def main():
    from .worker import investigate
    settings = Settings.from_env()
    settings.validate_startup()
    store = RunStore(Database(settings.database_url))
    while True:
        try:
            dispatch_pending(store, investigate.delay)
        except Exception:
            logging.getLogger(__name__).error('Outbox delivery failed; messages remain pending')
        time.sleep(2)


if __name__ == '__main__':
    main()
