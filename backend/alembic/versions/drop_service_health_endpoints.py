"""remove the obsolete project health endpoint integration

Revision ID: 0009
Revises: 0008
"""

from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("project_service_endpoints")


def downgrade() -> None:
    raise RuntimeError("The obsolete project health endpoint cannot be restored")
