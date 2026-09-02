"""whatsapp core

Revision ID: 20260626_0003
Revises: 20260625_0002
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260626_0003"
down_revision = "20260625_0002"
branch_labels = None
depends_on = None


json_type = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "whatsapp_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("colaborador_id", sa.Integer(), sa.ForeignKey("colaboradores.id"), nullable=True),
        sa.Column("matricula", sa.String(length=30), nullable=True),
        sa.Column("nome", sa.String(length=220), nullable=True),
        sa.Column("email", sa.String(length=220), nullable=True),
        sa.Column("cargo", sa.String(length=140), nullable=True),
        sa.Column("telefone", sa.String(length=40), nullable=True),
        sa.Column("telefone_formatado", sa.String(length=30), nullable=False),
        sa.Column("fonte", sa.String(length=80), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ativo"),
        sa.Column("payload", json_type, nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_contacts_id", "whatsapp_contacts", ["id"])
    op.create_index("ix_whatsapp_contacts_colaborador_id", "whatsapp_contacts", ["colaborador_id"])
    op.create_index("ix_whatsapp_contacts_matricula", "whatsapp_contacts", ["matricula"])
    op.create_index("ix_whatsapp_contacts_nome", "whatsapp_contacts", ["nome"])
    op.create_index("ix_whatsapp_contacts_email", "whatsapp_contacts", ["email"])
    op.create_index("ix_whatsapp_contacts_cargo", "whatsapp_contacts", ["cargo"])
    op.create_index("ix_whatsapp_contacts_telefone_formatado", "whatsapp_contacts", ["telefone_formatado"], unique=True)
    op.create_index("ix_whatsapp_contacts_fonte", "whatsapp_contacts", ["fonte"])
    op.create_index("ix_whatsapp_contacts_status", "whatsapp_contacts", ["status"])
    op.create_index("ix_whatsapp_contacts_created_at", "whatsapp_contacts", ["created_at"])

    op.create_table(
        "whatsapp_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("documento_id", sa.Integer(), sa.ForeignKey("documentos.id"), nullable=True),
        sa.Column("colaborador_id", sa.Integer(), sa.ForeignKey("colaboradores.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("telefone", sa.String(length=30), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("tipo_mensagem", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pendente"),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("motivo_bloqueio", sa.Text(), nullable=True),
        sa.Column("agendado_para", sa.DateTime(), nullable=True),
        sa.Column("enviado_em", sa.DateTime(), nullable=True),
        sa.Column("payload", json_type, nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_queue_id", "whatsapp_queue", ["id"])
    op.create_index("ix_whatsapp_queue_documento_id", "whatsapp_queue", ["documento_id"])
    op.create_index("ix_whatsapp_queue_colaborador_id", "whatsapp_queue", ["colaborador_id"])
    op.create_index("ix_whatsapp_queue_usuario_id", "whatsapp_queue", ["usuario_id"])
    op.create_index("ix_whatsapp_queue_telefone", "whatsapp_queue", ["telefone"])
    op.create_index("ix_whatsapp_queue_tipo_mensagem", "whatsapp_queue", ["tipo_mensagem"])
    op.create_index("ix_whatsapp_queue_status", "whatsapp_queue", ["status"])
    op.create_index("ix_whatsapp_queue_agendado_para", "whatsapp_queue", ["agendado_para"])
    op.create_index("ix_whatsapp_queue_enviado_em", "whatsapp_queue", ["enviado_em"])
    op.create_index("ix_whatsapp_queue_created_at", "whatsapp_queue", ["created_at"])

    op.create_table(
        "whatsapp_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queue_id", sa.Integer(), sa.ForeignKey("whatsapp_queue.id"), nullable=True),
        sa.Column("documento_id", sa.Integer(), sa.ForeignKey("documentos.id"), nullable=True),
        sa.Column("colaborador_id", sa.Integer(), sa.ForeignKey("colaboradores.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("telefone", sa.String(length=30), nullable=True),
        sa.Column("mensagem", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("tipo_mensagem", sa.String(length=60), nullable=True),
        sa.Column("motivo_bloqueio", sa.Text(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("payload", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_history_id", "whatsapp_history", ["id"])
    op.create_index("ix_whatsapp_history_queue_id", "whatsapp_history", ["queue_id"])
    op.create_index("ix_whatsapp_history_documento_id", "whatsapp_history", ["documento_id"])
    op.create_index("ix_whatsapp_history_colaborador_id", "whatsapp_history", ["colaborador_id"])
    op.create_index("ix_whatsapp_history_usuario_id", "whatsapp_history", ["usuario_id"])
    op.create_index("ix_whatsapp_history_telefone", "whatsapp_history", ["telefone"])
    op.create_index("ix_whatsapp_history_status", "whatsapp_history", ["status"])
    op.create_index("ix_whatsapp_history_tipo_mensagem", "whatsapp_history", ["tipo_mensagem"])
    op.create_index("ix_whatsapp_history_created_at", "whatsapp_history", ["created_at"])


def downgrade() -> None:
    op.drop_table("whatsapp_history")
    op.drop_table("whatsapp_queue")
    op.drop_table("whatsapp_contacts")
