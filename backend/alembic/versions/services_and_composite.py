"""services layer + composite rule conditions

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_services_project_id", "services", ["project_id"])

    # nullable service_id on each target table (backward compatible: NULL = ungrouped)
    for table, col in [
        ("project_process_targets", "service_id"),
        ("project_log_sources", "service_id"),
        ("project_docker_targets", "service_id"),
        ("project_database_profiles", "service_id"),
        ("project_service_endpoints", "service_id"),
        ("project_metrics_sources", "service_id"),
    ]:
        op.add_column(table, sa.Column(col, sa.Uuid(), nullable=True))
        op.create_index(f"ix_{table}_{col}", table, [col])
        op.create_foreign_key(f"fk_{table}_{col}", table, "services", [col], ["id"], ondelete="SET NULL")

    op.add_column("monitoring_rules", sa.Column("conditions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("monitoring_rules", "conditions")
    for table, col in [
        ("project_metrics_sources", "service_id"),
        ("project_service_endpoints", "service_id"),
        ("project_database_profiles", "service_id"),
        ("project_docker_targets", "service_id"),
        ("project_log_sources", "service_id"),
        ("project_process_targets", "service_id"),
    ]:
        op.drop_constraint(f"fk_{table}_{col}", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_{col}", table_name=table)
        op.drop_column(table, col)
    op.drop_index("ix_services_project_id", table_name="services")
    op.drop_table("services")
