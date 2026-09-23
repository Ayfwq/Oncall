"""Normalize recovered incidents and protect active correlation keys.

Revision ID: 0014
Revises: 0013
"""

from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A previous agent race could leave a non-resolved status together with a
    # resolved_at timestamp. Treat the timestamp as authoritative so those
    # rows no longer participate in active correlation.
    op.execute(
        """
        UPDATE incidents
        SET status = 'resolved'
        WHERE resolved_at IS NOT NULL AND status <> 'resolved'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_incident_active_fingerprint
        ON incidents (project_id, fingerprint)
        WHERE resolved_at IS NULL
          AND status IN ('open', 'investigating', 'diagnosed')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_incident_active_fingerprint")
