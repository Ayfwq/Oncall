"""support saved and selectable model connections

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("model_profiles"):
        op.create_table(
            "model_profiles",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("base_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("model", sa.String(200), nullable=False),
            sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_model_profiles_kind", "model_profiles", ["kind"])

    # Keep the most recently created active profile if older work left more
    # than one enabled row for a model kind.
    op.execute(
        sa.text(
            "UPDATE model_profiles AS p SET enabled = false "
            "WHERE enabled IS TRUE AND id IN ("
            "SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY kind ORDER BY created_at DESC, id DESC) AS rn "
            "FROM model_profiles WHERE enabled IS TRUE) AS ranked WHERE rn > 1)"
        )
    )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("model_profiles")}
    if "uq_model_profiles_active_kind" not in indexes:
        op.create_index(
            "uq_model_profiles_active_kind",
            "model_profiles",
            ["kind"],
            unique=True,
            postgresql_where=sa.text("enabled IS TRUE"),
        )


def downgrade() -> None:
    op.drop_index("uq_model_profiles_active_kind", table_name="model_profiles")
