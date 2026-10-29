"""Initial isolated platform tables; legacy incident tables are untouched."""
from alembic import op
import sqlalchemy as sa

revision = '0001_platform'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('platform_runs',
        sa.Column('id', sa.String(64), primary_key=True), sa.Column('org', sa.String(100), nullable=False),
        sa.Column('asset', sa.String(200), nullable=False), sa.Column('owner', sa.String(200), nullable=False),
        sa.Column('status', sa.String(40), nullable=False), sa.Column('state', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('lease_owner', sa.String(100), nullable=False),
        sa.Column('fence', sa.Integer(), nullable=False), sa.Column('lease_until', sa.Float(), nullable=False),
        sa.Column('created', sa.Float(), nullable=False), sa.Column('updated', sa.Float(), nullable=False))
    op.create_index('ix_platform_runs_org', 'platform_runs', ['org'])
    op.create_index('ix_platform_runs_status', 'platform_runs', ['status'])
    op.create_table('platform_events', sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('run_id', sa.String(64), nullable=False), sa.Column('kind', sa.String(60), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False), sa.Column('at', sa.Float(), nullable=False))
    op.create_index('ix_platform_events_run_id', 'platform_events', ['run_id'])
    op.create_table('platform_audit', sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('org', sa.String(100), nullable=False), sa.Column('asset', sa.String(200), nullable=False),
        sa.Column('subject', sa.String(200), nullable=False), sa.Column('action', sa.String(60), nullable=False),
        sa.Column('target', sa.String(64), nullable=False), sa.Column('detail', sa.JSON(), nullable=False), sa.Column('at', sa.Float(), nullable=False))
    op.create_table('platform_outbox', sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('run_id', sa.String(64), nullable=False), sa.Column('published', sa.Float()), sa.Column('created', sa.Float(), nullable=False))
    op.create_index('ix_platform_outbox_run_id', 'platform_outbox', ['run_id'])
    op.create_table('platform_actions', sa.Column('id', sa.String(64), primary_key=True), sa.Column('run_id', sa.String(64), nullable=False),
        sa.Column('org', sa.String(100), nullable=False), sa.Column('asset', sa.String(200), nullable=False),
        sa.Column('status', sa.String(40), nullable=False), sa.Column('document', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('created', sa.Float(), nullable=False))
    op.create_index('ix_platform_actions_run_id', 'platform_actions', ['run_id'])
    op.create_table('platform_sessions', sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('subject', sa.String(200), nullable=False), sa.Column('expires', sa.Float(), nullable=False),
        sa.Column('kind', sa.String(30), nullable=False), sa.Column('document', sa.JSON(), nullable=False))


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled. Restore a verified database backup instead.')
