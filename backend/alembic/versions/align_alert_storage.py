"""Remove the final local collector table and align alert storage.

Revision ID: 0012
Revises: 0011
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "log_cursors" in inspector.get_table_names():
        op.drop_table("log_cursors")

    if "alertmanager_alerts" not in inspector.get_table_names():
        return

    columns = {column["name"]: column for column in inspector.get_columns("alertmanager_alerts")}
    for name in ("labels", "annotations"):
        if name in columns and not isinstance(columns[name]["type"], postgresql.JSONB):
            op.alter_column(
                "alertmanager_alerts",
                name,
                existing_type=sa.JSON(),
                type_=postgresql.JSONB(),
                postgresql_using=f"{name}::jsonb",
            )

    for constraint in inspector.get_unique_constraints("alertmanager_alerts"):
        if constraint.get("column_names") == ["fingerprint"]:
            op.drop_constraint(constraint["name"], "alertmanager_alerts", type_="unique")
    index_names = {index["name"] for index in inspector.get_indexes("alertmanager_alerts")}
    if "ix_alertmanager_alerts_fingerprint" not in index_names:
        op.create_index(
            "ix_alertmanager_alerts_fingerprint",
            "alertmanager_alerts",
            ["fingerprint"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ix_alertmanager_alerts_fingerprint", table_name="alertmanager_alerts")
    op.create_unique_constraint(
        "alertmanager_alerts_fingerprint_key",
        "alertmanager_alerts",
        ["fingerprint"],
    )
    op.alter_column(
        "alertmanager_alerts",
        "annotations",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        postgresql_using="annotations::json",
    )
    op.alter_column(
        "alertmanager_alerts",
        "labels",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        postgresql_using="labels::json",
    )
