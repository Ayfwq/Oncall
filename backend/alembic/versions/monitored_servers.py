"""add remotely monitored servers

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitored_servers",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("node_metrics_url", sa.Text(), nullable=False),
        sa.Column("gpu_metrics_url", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "name", name="uq_monitored_server_user_name"),
    )
    op.create_index("ix_monitored_servers_user_id", "monitored_servers", ["user_id"])
    op.add_column("projects", sa.Column("server_id", sa.Uuid(), nullable=True))
    op.create_index("ix_projects_server_id", "projects", ["server_id"])
    op.create_foreign_key("fk_projects_server_id", "projects", "monitored_servers", ["server_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_projects_server_id", "projects", type_="foreignkey")
    op.drop_index("ix_projects_server_id", table_name="projects")
    op.drop_column("projects", "server_id")
    op.drop_index("ix_monitored_servers_user_id", table_name="monitored_servers")
    op.drop_table("monitored_servers")
