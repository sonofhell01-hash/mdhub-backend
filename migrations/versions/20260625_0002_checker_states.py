"""checker states

Revision ID: 20260625_0002
Revises: 20260625_0001
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260625_0002"
down_revision = "20260625_0001"
branch_labels = None
depends_on = None


json_type = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "checker_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fonte", sa.String(length=80), nullable=False),
        sa.Column("modulo", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pendente"),
        sa.Column("ultimo_cursor", sa.String(length=120), nullable=True),
        sa.Column("total_registros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultima_contagem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", json_type, nullable=True),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_checker_states_id", "checker_states", ["id"])
    op.create_index("ix_checker_states_fonte", "checker_states", ["fonte"])
    op.create_index("ix_checker_states_modulo", "checker_states", ["modulo"])
    op.create_index("ix_checker_states_status", "checker_states", ["status"])
    op.create_index("ix_checker_states_ultimo_cursor", "checker_states", ["ultimo_cursor"])
    op.create_index("ix_checker_states_checked_at", "checker_states", ["checked_at"])
    op.create_index("uq_checker_states_fonte_modulo", "checker_states", ["fonte", "modulo"], unique=True)


def downgrade() -> None:
    op.drop_table("checker_states")
