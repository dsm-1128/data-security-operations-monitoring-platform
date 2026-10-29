"""Use double precision for epoch timestamps and worker leases."""
from alembic import op
import sqlalchemy as sa

revision = '0002_timestamp_precision'
down_revision = '0001_platform'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'mysql':
        for table, columns in {
            'platform_runs': ['lease_until', 'created', 'updated'],
            'platform_events': ['at'], 'platform_audit': ['at'],
            'platform_outbox': ['published', 'created'], 'platform_actions': ['created'],
            'platform_sessions': ['expires'],
        }.items():
            for column in columns:
                op.alter_column(table, column, existing_type=sa.Float(), type_=sa.Float(precision=53),
                    existing_nullable=(table == 'platform_outbox' and column == 'published'))
        # Recover exact creation time where the original JSON retained it.
        op.execute("UPDATE platform_runs SET created=CAST(JSON_UNQUOTE(JSON_EXTRACT(state, '$.created')) AS DOUBLE) WHERE JSON_EXTRACT(state, '$.created') IS NOT NULL")


def downgrade():
    raise RuntimeError('Reducing timestamp precision is unsafe and unsupported.')
