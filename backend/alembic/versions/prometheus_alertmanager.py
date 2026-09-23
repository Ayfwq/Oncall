"""Prometheus/Alertmanager becomes the alert decision pipeline.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def _drop_if_present(name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    if name in inspector.get_table_names():
        op.drop_table(name)


def upgrade() -> None:
    # These tables belonged to the removed in-process detector/state machine.
    # Alertmanager now owns firing/recovery state and its fingerprints are the
    # only durable alert identity kept by the application.
    for table in (
        'incident_signals', 'alert_events', 'monitoring_rule_states',
        'monitoring_baselines', 'monitoring_rules', 'monitoring_runs',
        'metric_samples', 'metric_cursors',
    ):
        _drop_if_present(table)

    inspector = sa.inspect(op.get_bind())
    server_columns = {x['name'] for x in inspector.get_columns('monitored_servers')}
    if 'container_metrics_url' not in server_columns:
        op.add_column('monitored_servers', sa.Column('container_metrics_url', sa.Text(), nullable=True))

    op.create_table(
        'alertmanager_alerts',
        sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
        sa.Column('fingerprint', sa.String(128), nullable=False, unique=True),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='firing'),
        sa.Column('alertname', sa.String(200), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False, server_default='warning'),
        sa.Column('labels', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('annotations', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_alertmanager_alerts_project_id', 'alertmanager_alerts', ['project_id'])
    op.create_index('ix_alertmanager_alerts_status', 'alertmanager_alerts', ['status'])
    op.create_index('ix_alertmanager_alerts_received_at', 'alertmanager_alerts', ['received_at'])


def downgrade() -> None:
    op.drop_index('ix_alertmanager_alerts_received_at', table_name='alertmanager_alerts')
    op.drop_index('ix_alertmanager_alerts_status', table_name='alertmanager_alerts')
    op.drop_index('ix_alertmanager_alerts_project_id', table_name='alertmanager_alerts')
    op.drop_table('alertmanager_alerts')
    op.drop_column('monitored_servers', 'container_metrics_url')
