"""add metrics sources and metric cursors

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_metrics_sources",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default="app"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.String(20), nullable=False, server_default="none"),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=True),
        sa.Column("scrape_timeout_ms", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("route_label", sa.String(80), nullable=False, server_default="handler"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_project_metrics_sources_project_id", "project_metrics_sources", ["project_id"])

    op.create_table(
        "metric_cursors",
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("resource_key", sa.String(200), nullable=False),
        sa.Column("metric_key", sa.String(160), nullable=False),
        sa.Column("last_value", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("last_ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("project_id", "resource_key", "metric_key"),
    )
    op.create_index("ix_metric_cursor_lookup", "metric_cursors", ["project_id", "resource_key", "metric_key"])


def downgrade() -> None:
    op.drop_index("ix_metric_cursor_lookup", table_name="metric_cursors")
    op.drop_table("metric_cursors")
    op.drop_index("ix_project_metrics_sources_project_id", table_name="project_metrics_sources")
    op.drop_table("project_metrics_sources")
