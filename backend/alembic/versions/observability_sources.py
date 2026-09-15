"""remote collector connection for logs and database diagnostics

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("monitored_servers", sa.Column("collector_url", sa.Text(), nullable=True))
    op.add_column("monitored_servers", sa.Column("encrypted_collector_token", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("monitored_servers", "encrypted_collector_token")
    op.drop_column("monitored_servers", "collector_url")
