"""require cAdvisor for every monitored Docker server

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Do not invent an endpoint for an existing server. Refuse the migration
    # until every monitored Docker host has a real cAdvisor URL configured.
    op.alter_column(
        "monitored_servers",
        "container_metrics_url",
        existing_type=sa.Text(),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "monitored_servers",
        "container_metrics_url",
        existing_type=sa.Text(),
        nullable=True,
    )
