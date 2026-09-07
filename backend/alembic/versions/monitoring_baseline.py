"""Python onboarding metadata and adaptive monitoring rules

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("environment", sa.String(40), nullable=False, server_default="local"))
    op.add_column("monitoring_rules", sa.Column("detection_mode", sa.String(20), nullable=False, server_default="threshold"))
    op.add_column("monitoring_rules", sa.Column("baseline_window", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("monitoring_rules", sa.Column("baseline_min_samples", sa.Integer(), nullable=False, server_default="12"))
    op.add_column("monitoring_rules", sa.Column("baseline_z_score", sa.Float(), nullable=False, server_default="3.0"))
    op.add_column("monitoring_rules", sa.Column("baseline_recovery_z_score", sa.Float(), nullable=False, server_default="2.0"))
    op.create_table(
        "monitoring_baselines",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_key", sa.String(160), nullable=False),
        sa.Column("resource_key", sa.String(200), nullable=False, server_default="default"),
        sa.Column("samples", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "metric_key", "resource_key", name="uq_monitoring_baseline_key"),
    )
    op.create_index("ix_monitoring_baselines_project_id", "monitoring_baselines", ["project_id"])
    op.create_index("ix_monitoring_baselines_metric_key", "monitoring_baselines", ["metric_key"])


def downgrade() -> None:
    op.drop_index("ix_monitoring_baselines_metric_key", table_name="monitoring_baselines")
    op.drop_index("ix_monitoring_baselines_project_id", table_name="monitoring_baselines")
    op.drop_table("monitoring_baselines")
    for name in ("baseline_recovery_z_score", "baseline_z_score", "baseline_min_samples", "baseline_window", "detection_mode"):
        op.drop_column("monitoring_rules", name)
    op.drop_column("projects", "environment")
