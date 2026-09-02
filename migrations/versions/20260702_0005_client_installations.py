"""client installations registry

Revision ID: 20260702_0005
Revises: 20260626_0004
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0005"
down_revision = "20260626_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_installations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("hostname", sa.String(length=120), nullable=True),
        sa.Column("windows_user", sa.String(length=120), nullable=True),
        sa.Column("technician_email", sa.String(length=160), nullable=True),
        sa.Column("technician_name", sa.String(length=160), nullable=True),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("backend_version", sa.String(length=40), nullable=True),
        sa.Column("install_mode", sa.String(length=40), nullable=True),
        sa.Column("last_ip", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ativo"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_client_installations_client_id"), "client_installations", ["client_id"], unique=True)
    op.create_index(op.f("ix_client_installations_hostname"), "client_installations", ["hostname"], unique=False)
    op.create_index(op.f("ix_client_installations_windows_user"), "client_installations", ["windows_user"], unique=False)
    op.create_index(op.f("ix_client_installations_technician_email"), "client_installations", ["technician_email"], unique=False)
    op.create_index(op.f("ix_client_installations_technician_name"), "client_installations", ["technician_name"], unique=False)
    op.create_index(op.f("ix_client_installations_app_version"), "client_installations", ["app_version"], unique=False)
    op.create_index(op.f("ix_client_installations_backend_version"), "client_installations", ["backend_version"], unique=False)
    op.create_index(op.f("ix_client_installations_install_mode"), "client_installations", ["install_mode"], unique=False)
    op.create_index(op.f("ix_client_installations_last_ip"), "client_installations", ["last_ip"], unique=False)
    op.create_index(op.f("ix_client_installations_status"), "client_installations", ["status"], unique=False)
    op.create_index(op.f("ix_client_installations_last_seen_at"), "client_installations", ["last_seen_at"], unique=False)
    op.create_index(op.f("ix_client_installations_created_at"), "client_installations", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_client_installations_created_at"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_last_seen_at"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_status"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_last_ip"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_install_mode"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_backend_version"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_app_version"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_technician_name"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_technician_email"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_windows_user"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_hostname"), table_name="client_installations")
    op.drop_index(op.f("ix_client_installations_client_id"), table_name="client_installations")
    op.drop_table("client_installations")
