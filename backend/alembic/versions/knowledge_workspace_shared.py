"""Make the knowledge base workspace-wide instead of project-scoped.

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("knowledge_documents")}
    if "project_scope" not in columns:
        return

    # Existing documents are intentionally retained. They become shared
    # documents when the obsolete project boundary is removed.
    for index in inspector.get_indexes("knowledge_documents"):
        if index.get("column_names") == ["project_scope"]:
            op.drop_index(index["name"], table_name="knowledge_documents")
    for foreign_key in inspector.get_foreign_keys("knowledge_documents"):
        if foreign_key.get("constrained_columns") == ["project_scope"]:
            op.drop_constraint(foreign_key["name"], "knowledge_documents", type_="foreignkey")
    op.drop_column("knowledge_documents", "project_scope")


def downgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("project_scope", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "knowledge_documents_project_scope_fkey",
        "knowledge_documents",
        "projects",
        ["project_scope"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_knowledge_documents_project_scope",
        "knowledge_documents",
        ["project_scope"],
    )
