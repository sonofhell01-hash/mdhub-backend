"""Persistent operational templates for Laudo and RAT.

Revision ID: 20260716_0007
Revises: 20260716_0006
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260716_0007"
down_revision = "20260716_0006"
branch_labels = None
depends_on = None


json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "operational_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=50), server_default="", nullable=False),
        sa.Column("label", sa.String(length=140), nullable=False),
        sa.Column("label_key", sa.String(length=160), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("source", sa.String(length=30), server_default="manual", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_type", "category", "label_key", name="uq_operational_template_identity"),
    )
    for column in ("id", "document_type", "category", "active", "created_by_user_id", "created_at"):
        op.create_index(op.f(f"ix_operational_templates_{column}"), "operational_templates", [column], unique=False)


def downgrade() -> None:
    for column in ("created_at", "created_by_user_id", "active", "category", "document_type", "id"):
        op.drop_index(op.f(f"ix_operational_templates_{column}"), table_name="operational_templates")
    op.drop_table("operational_templates")
