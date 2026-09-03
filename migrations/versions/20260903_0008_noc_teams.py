"""Central NOC: equipes, usuario_equipes e usuarios.perfil_noc.

Revision ID: 20260903_0008
Revises: 20260716_0007
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260903_0008"
down_revision = "20260716_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("codigo", sa.String(length=60), nullable=False),
        sa.Column("localizacao", sa.String(length=160), nullable=True),
        sa.Column("ativa", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nome", name="uq_equipes_nome"),
        sa.UniqueConstraint("codigo", name="uq_equipes_codigo"),
    )
    op.create_index(op.f("ix_equipes_id"), "equipes", ["id"], unique=False)
    op.create_index(op.f("ix_equipes_nome"), "equipes", ["nome"], unique=False)
    op.create_index(op.f("ix_equipes_codigo"), "equipes", ["codigo"], unique=False)

    op.create_table(
        "usuario_equipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("equipe_id", sa.Integer(), nullable=False),
        sa.Column("ativa", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("principal", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["equipe_id"], ["equipes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("usuario_id", "equipe_id", name="uq_usuario_equipes_usuario_equipe"),
    )
    op.create_index(op.f("ix_usuario_equipes_id"), "usuario_equipes", ["id"], unique=False)
    op.create_index(op.f("ix_usuario_equipes_usuario_id"), "usuario_equipes", ["usuario_id"], unique=False)
    op.create_index(op.f("ix_usuario_equipes_equipe_id"), "usuario_equipes", ["equipe_id"], unique=False)

    op.add_column(
        "usuarios",
        sa.Column("perfil_noc", sa.String(length=30), server_default="tecnico", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("usuarios", "perfil_noc")

    op.drop_index(op.f("ix_usuario_equipes_equipe_id"), table_name="usuario_equipes")
    op.drop_index(op.f("ix_usuario_equipes_usuario_id"), table_name="usuario_equipes")
    op.drop_index(op.f("ix_usuario_equipes_id"), table_name="usuario_equipes")
    op.drop_table("usuario_equipes")

    op.drop_index(op.f("ix_equipes_codigo"), table_name="equipes")
    op.drop_index(op.f("ix_equipes_nome"), table_name="equipes")
    op.drop_index(op.f("ix_equipes_id"), table_name="equipes")
    op.drop_table("equipes")
