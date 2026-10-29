from alembic import context
from sqlalchemy import create_engine

from app.platform.config import Settings
from app.platform.database import metadata

url = Settings.from_env().database_url
if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
