"""Persist explicit cross-conversation memory facts.

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_facts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_facts_user_id", "memory_facts", ["user_id"])
    op.create_index("ix_memory_facts_project_id", "memory_facts", ["project_id"])
    op.create_index("ix_memory_facts_active", "memory_facts", ["active"])


def downgrade() -> None:
    op.drop_index("ix_memory_facts_active", table_name="memory_facts")
    op.drop_index("ix_memory_facts_project_id", table_name="memory_facts")
    op.drop_index("ix_memory_facts_user_id", table_name="memory_facts")
    op.drop_table("memory_facts")
