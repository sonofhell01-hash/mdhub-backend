"""AI audit metadata for the controlled Ollama pilot.

Revision ID: 20260716_0006
Revises: 20260702_0005
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260716_0006"
down_revision = "20260702_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("use_case", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("input_chars", sa.Integer(), nullable=False),
        sa.Column("output_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(op.f("ix_ai_audit_events_request_id"), "ai_audit_events", ["request_id"], unique=True)
    op.create_index(op.f("ix_ai_audit_events_created_at"), "ai_audit_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_audit_events_user_id"), "ai_audit_events", ["user_id"], unique=False)
    op.create_index(op.f("ix_ai_audit_events_use_case"), "ai_audit_events", ["use_case"], unique=False)
    op.create_index(op.f("ix_ai_audit_events_status"), "ai_audit_events", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_audit_events_status"), table_name="ai_audit_events")
    op.drop_index(op.f("ix_ai_audit_events_use_case"), table_name="ai_audit_events")
    op.drop_index(op.f("ix_ai_audit_events_user_id"), table_name="ai_audit_events")
    op.drop_index(op.f("ix_ai_audit_events_created_at"), table_name="ai_audit_events")
    op.drop_index(op.f("ix_ai_audit_events_request_id"), table_name="ai_audit_events")
    op.drop_table("ai_audit_events")
