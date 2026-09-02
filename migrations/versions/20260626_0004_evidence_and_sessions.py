"""asset evidences and persistent midiasimples sessions

Revision ID: 20260626_0004
Revises: 20260626_0003
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260626_0004"
down_revision = "20260626_0003"
branch_labels = None
depends_on = None


json_type = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "asset_evidences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fonte", sa.String(length=80), nullable=False),
        sa.Column("modulo", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=True),
        sa.Column("serial", sa.String(length=80), nullable=True),
        sa.Column("patrimonio", sa.String(length=80), nullable=True),
        sa.Column("hostname", sa.String(length=100), nullable=True),
        sa.Column("matricula", sa.String(length=30), nullable=True),
        sa.Column("nome", sa.String(length=220), nullable=True),
        sa.Column("email", sa.String(length=220), nullable=True),
        sa.Column("categoria", sa.String(length=80), nullable=True),
        sa.Column("marca", sa.String(length=100), nullable=True),
        sa.Column("modelo", sa.String(length=140), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=True),
        sa.Column("confidence", sa.String(length=30), nullable=False, server_default="media"),
        sa.Column("evidence_at", sa.DateTime(), nullable=True),
        sa.Column("payload", json_type, nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    for column in (
        "id",
        "fonte",
        "modulo",
        "external_id",
        "serial",
        "patrimonio",
        "hostname",
        "matricula",
        "nome",
        "email",
        "categoria",
        "status",
        "confidence",
        "evidence_at",
        "created_at",
    ):
        op.create_index(f"ix_asset_evidences_{column}", "asset_evidences", [column])

    op.create_table(
        "midiasimples_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=160), nullable=False),
        sa.Column("user_name", sa.String(length=160), nullable=True),
        sa.Column("base_url", sa.String(length=240), nullable=False),
        sa.Column("session_data", json_type, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ativa"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_midiasimples_sessions_id", "midiasimples_sessions", ["id"])
    op.create_index("ix_midiasimples_sessions_email", "midiasimples_sessions", ["email"], unique=True)
    op.create_index("ix_midiasimples_sessions_status", "midiasimples_sessions", ["status"])
    op.create_index("ix_midiasimples_sessions_last_used_at", "midiasimples_sessions", ["last_used_at"])
    op.create_index("ix_midiasimples_sessions_expires_at", "midiasimples_sessions", ["expires_at"])
    op.create_index("ix_midiasimples_sessions_created_at", "midiasimples_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_table("midiasimples_sessions")
    op.drop_table("asset_evidences")
