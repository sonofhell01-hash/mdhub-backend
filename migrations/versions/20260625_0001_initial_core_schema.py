"""initial core schema

Revision ID: 20260625_0001
Revises:
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260625_0001"
down_revision = None
branch_labels = None
depends_on = None


json_type = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("apelido", sa.String(length=80), nullable=True),
        sa.Column("email", sa.String(length=160), nullable=False),
        sa.Column("midiasimples_id", sa.Integer(), nullable=True),
        sa.Column("perfil", sa.String(length=30), nullable=False, server_default="tecnico"),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ultimo_login", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_usuarios_id", "usuarios", ["id"])
    op.create_index("ix_usuarios_email", "usuarios", ["email"], unique=True)
    op.create_index("ix_usuarios_midiasimples_id", "usuarios", ["midiasimples_id"], unique=True)

    op.create_table(
        "colaboradores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("matricula", sa.String(length=30), nullable=False),
        sa.Column("nome", sa.String(length=220), nullable=False),
        sa.Column("email", sa.String(length=220), nullable=True),
        sa.Column("telefone", sa.String(length=40), nullable=True),
        sa.Column("cargo", sa.String(length=140), nullable=True),
        sa.Column("regional", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ativo"),
        sa.Column("fonte", sa.String(length=80), nullable=False, server_default="manual"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_colaboradores_id", "colaboradores", ["id"])
    op.create_index("ix_colaboradores_matricula", "colaboradores", ["matricula"], unique=True)
    op.create_index("ix_colaboradores_email", "colaboradores", ["email"])

    op.create_table(
        "equipamentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("colaborador_id", sa.Integer(), sa.ForeignKey("colaboradores.id"), nullable=True),
        sa.Column("serial", sa.String(length=80), nullable=False),
        sa.Column("patrimonio", sa.String(length=80), nullable=True),
        sa.Column("hostname", sa.String(length=100), nullable=True),
        sa.Column("categoria", sa.String(length=80), nullable=True),
        sa.Column("marca", sa.String(length=100), nullable=True),
        sa.Column("modelo", sa.String(length=140), nullable=True),
        sa.Column("modelo_tecnico", sa.String(length=140), nullable=True),
        sa.Column("nota_fiscal", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ativo"),
        sa.Column("fonte", sa.String(length=80), nullable=False, server_default="manual"),
        sa.Column("payload", json_type, nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_equipamentos_id", "equipamentos", ["id"])
    op.create_index("ix_equipamentos_colaborador_id", "equipamentos", ["colaborador_id"])
    op.create_index("ix_equipamentos_serial", "equipamentos", ["serial"], unique=True)
    op.create_index("ix_equipamentos_patrimonio", "equipamentos", ["patrimonio"])
    op.create_index("ix_equipamentos_hostname", "equipamentos", ["hostname"])
    op.create_index("ix_equipamentos_status", "equipamentos", ["status"])

    op.create_table(
        "documentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tipo", sa.String(length=50), nullable=False),
        sa.Column("colaborador_id", sa.Integer(), sa.ForeignKey("colaboradores.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("numero_chamado", sa.String(length=80), nullable=True),
        sa.Column("midiasimples_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="criado"),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("response_payload", json_type, nullable=True),
        sa.Column("sync_pendente", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("enviado_em", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_documentos_id", "documentos", ["id"])
    op.create_index("ix_documentos_tipo", "documentos", ["tipo"])
    op.create_index("ix_documentos_colaborador_id", "documentos", ["colaborador_id"])
    op.create_index("ix_documentos_usuario_id", "documentos", ["usuario_id"])
    op.create_index("ix_documentos_numero_chamado", "documentos", ["numero_chamado"])
    op.create_index("ix_documentos_midiasimples_id", "documentos", ["midiasimples_id"])
    op.create_index("ix_documentos_status", "documentos", ["status"])
    op.create_index("ix_documentos_sync_pendente", "documentos", ["sync_pendente"])
    op.create_index("ix_documentos_created_at", "documentos", ["created_at"])

    op.create_table(
        "logs_auditoria",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("acao", sa.String(length=80), nullable=False),
        sa.Column("modulo", sa.String(length=80), nullable=True),
        sa.Column("resultado", sa.String(length=40), nullable=True),
        sa.Column("payload", json_type, nullable=True),
        sa.Column("resposta", json_type, nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_logs_auditoria_id", "logs_auditoria", ["id"])
    op.create_index("ix_logs_auditoria_usuario_id", "logs_auditoria", ["usuario_id"])
    op.create_index("ix_logs_auditoria_acao", "logs_auditoria", ["acao"])
    op.create_index("ix_logs_auditoria_modulo", "logs_auditoria", ["modulo"])
    op.create_index("ix_logs_auditoria_resultado", "logs_auditoria", ["resultado"])
    op.create_index("ix_logs_auditoria_created_at", "logs_auditoria", ["created_at"])

    op.create_table(
        "sync_pendente",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("tipo", sa.String(length=60), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pendente"),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("ultima_tentativa", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sync_pendente_id", "sync_pendente", ["id"])
    op.create_index("ix_sync_pendente_usuario_id", "sync_pendente", ["usuario_id"])
    op.create_index("ix_sync_pendente_tipo", "sync_pendente", ["tipo"])
    op.create_index("ix_sync_pendente_status", "sync_pendente", ["status"])
    op.create_index("ix_sync_pendente_created_at", "sync_pendente", ["created_at"])


def downgrade() -> None:
    op.drop_table("sync_pendente")
    op.drop_table("logs_auditoria")
    op.drop_table("documentos")
    op.drop_table("equipamentos")
    op.drop_table("colaboradores")
    op.drop_table("usuarios")
