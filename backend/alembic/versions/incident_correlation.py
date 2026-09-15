"""correlate detector signals into one incident

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_signals",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("monitoring_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_key", sa.String(200), nullable=False, server_default="default"),
        sa.Column("anomaly_type", sa.String(160), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="warning"),
        sa.Column("state", sa.String(20), nullable=False, server_default="firing"),
        sa.Column("last_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("incident_id", "rule_id", "resource_key", name="uq_incident_signal_rule_resource"),
    )
    op.create_index("ix_incident_signals_incident_id", "incident_signals", ["incident_id"])
    op.create_index("ix_incident_signals_rule_id", "incident_signals", ["rule_id"])
    op.create_index("ix_incident_signals_state", "incident_signals", ["state"])


def downgrade() -> None:
    op.drop_index("ix_incident_signals_state", table_name="incident_signals")
    op.drop_index("ix_incident_signals_rule_id", table_name="incident_signals")
    op.drop_index("ix_incident_signals_incident_id", table_name="incident_signals")
    op.drop_table("incident_signals")
