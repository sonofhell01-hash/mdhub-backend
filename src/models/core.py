from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from src.core.db_session import Base


JsonType = JSON().with_variant(JSONB, "postgresql")


class User(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    apelido: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    midiasimples_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    perfil: Mapped[str] = mapped_column(String(30), default="tecnico")
    # Autorizacao interna do MD HUB para a Central NOC (tecnico | admin | gestor_noc).
    # Deliberadamente separado de `perfil` (que hoje reflete outra coisa) para nao
    # copiar automaticamente permissoes externas do MidiaSimples (ex.: "Integration
    # admin") como se fossem autorizacao de equipe dentro do HUB.
    perfil_noc: Mapped[str] = mapped_column(String(30), default="tecnico", server_default="tecnico")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    documents: Mapped[list["Document"]] = relationship(back_populates="user")
    team_links: Mapped[list["UserTeam"]] = relationship(back_populates="user")


class Team(Base):
    """Equipe operacional (ex.: CEO RJ, PISA SP) usada para escopar a Central NOC."""

    __tablename__ = "equipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    codigo: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    localizacao: Mapped[str | None] = mapped_column(String(160))
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user_links: Mapped[list["UserTeam"]] = relationship(back_populates="team")


class UserTeam(Base):
    """Vinculo usuario<->equipe. Permite remanejamento, cobertura temporaria e
    multiplas equipes para gestores, sem apagar historico (usa `ativa`)."""

    __tablename__ = "usuario_equipes"
    __table_args__ = (UniqueConstraint("usuario_id", "equipe_id", name="uq_usuario_equipes_usuario_equipe"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True, nullable=False)
    equipe_id: Mapped[int] = mapped_column(ForeignKey("equipes.id"), index=True, nullable=False)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    principal: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="team_links")
    team: Mapped["Team"] = relationship(back_populates="user_links")


class Collaborator(Base):
    __tablename__ = "colaboradores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    matricula: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(220), nullable=False)
    email: Mapped[str | None] = mapped_column(String(220), index=True)
    telefone: Mapped[str | None] = mapped_column(String(40))
    cargo: Mapped[str | None] = mapped_column(String(140))
    regional: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="ativo")
    fonte: Mapped[str] = mapped_column(String(80), default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    equipments: Mapped[list["Equipment"]] = relationship(back_populates="collaborator")
    documents: Mapped[list["Document"]] = relationship(back_populates="collaborator")
    whatsapp_contacts: Mapped[list["WhatsAppContact"]] = relationship(back_populates="collaborator")


class Equipment(Base):
    __tablename__ = "equipamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    colaborador_id: Mapped[int | None] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    serial: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    patrimonio: Mapped[str | None] = mapped_column(String(80), index=True)
    hostname: Mapped[str | None] = mapped_column(String(100), index=True)
    categoria: Mapped[str | None] = mapped_column(String(80))
    marca: Mapped[str | None] = mapped_column(String(100))
    modelo: Mapped[str | None] = mapped_column(String(140))
    modelo_tecnico: Mapped[str | None] = mapped_column(String(140))
    nota_fiscal: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="ativo", index=True)
    fonte: Mapped[str] = mapped_column(String(80), default="manual")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    collaborator: Mapped[Collaborator | None] = relationship(back_populates="equipments")


class Document(Base):
    __tablename__ = "documentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tipo: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    colaborador_id: Mapped[int | None] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), index=True)
    numero_chamado: Mapped[str | None] = mapped_column(String(255), index=True)
    midiasimples_id: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(40), default="criado", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    sync_pendente: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sync_tentativas: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime)

    collaborator: Mapped[Collaborator | None] = relationship(back_populates="documents")
    user: Mapped[User | None] = relationship(back_populates="documents")


class AuditLog(Base):
    __tablename__ = "logs_auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), index=True)
    acao: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    modulo: Mapped[str | None] = mapped_column(String(80), index=True)
    resultado: Mapped[str | None] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    resposta: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    erro: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class SyncPending(Base):
    __tablename__ = "sync_pendente"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="pendente", index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_erro: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    ultima_tentativa: Mapped[datetime | None] = mapped_column(DateTime)


class CheckerState(Base):
    __tablename__ = "checker_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fonte: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    modulo: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pendente", index=True)
    ultimo_cursor: Mapped[str | None] = mapped_column(String(120), index=True)
    total_registros: Mapped[int] = mapped_column(Integer, default=0)
    ultima_contagem: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    ultimo_erro: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AssetEvidence(Base):
    __tablename__ = "asset_evidences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fonte: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    modulo: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    serial: Mapped[str | None] = mapped_column(String(80), index=True)
    patrimonio: Mapped[str | None] = mapped_column(String(80), index=True)
    hostname: Mapped[str | None] = mapped_column(String(100), index=True)
    matricula: Mapped[str | None] = mapped_column(String(30), index=True)
    nome: Mapped[str | None] = mapped_column(String(220), index=True)
    email: Mapped[str | None] = mapped_column(String(220), index=True)
    categoria: Mapped[str | None] = mapped_column(String(80), index=True)
    marca: Mapped[str | None] = mapped_column(String(100))
    modelo: Mapped[str | None] = mapped_column(String(140))
    status: Mapped[str | None] = mapped_column(String(80), index=True)
    confidence: Mapped[str] = mapped_column(String(30), default="media", index=True)
    evidence_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class MidiaSimplesSessionCache(Base):
    __tablename__ = "midiasimples_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(160))
    base_url: Mapped[str] = mapped_column(String(240), nullable=False)
    session_data: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="ativa", index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class WhatsAppContact(Base):
    __tablename__ = "whatsapp_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    colaborador_id: Mapped[int | None] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    matricula: Mapped[str | None] = mapped_column(String(30), index=True)
    nome: Mapped[str | None] = mapped_column(String(220), index=True)
    email: Mapped[str | None] = mapped_column(String(220), index=True)
    cargo: Mapped[str | None] = mapped_column(String(140), index=True)
    telefone: Mapped[str | None] = mapped_column(String(40))
    telefone_formatado: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    fonte: Mapped[str] = mapped_column(String(80), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(40), default="ativo", index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    collaborator: Mapped[Collaborator | None] = relationship(back_populates="whatsapp_contacts")


class WhatsAppQueue(Base):
    __tablename__ = "whatsapp_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    documento_id: Mapped[int | None] = mapped_column(ForeignKey("documentos.id"), index=True)
    colaborador_id: Mapped[int | None] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), index=True)
    telefone: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    mensagem: Mapped[str] = mapped_column(Text, nullable=False)
    tipo_mensagem: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pendente", index=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_erro: Mapped[str | None] = mapped_column(Text)
    motivo_bloqueio: Mapped[str | None] = mapped_column(Text)
    agendado_para: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class WhatsAppHistory(Base):
    __tablename__ = "whatsapp_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    queue_id: Mapped[int | None] = mapped_column(ForeignKey("whatsapp_queue.id"), index=True)
    documento_id: Mapped[int | None] = mapped_column(ForeignKey("documentos.id"), index=True)
    colaborador_id: Mapped[int | None] = mapped_column(ForeignKey("colaboradores.id"), index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), index=True)
    telefone: Mapped[str | None] = mapped_column(String(30), index=True)
    mensagem: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    tipo_mensagem: Mapped[str | None] = mapped_column(String(60), index=True)
    motivo_bloqueio: Mapped[str | None] = mapped_column(Text)
    erro: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class ClientInstallation(Base):
    __tablename__ = "client_installations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    client_id: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(120), index=True)
    windows_user: Mapped[str | None] = mapped_column(String(120), index=True)
    technician_email: Mapped[str | None] = mapped_column(String(160), index=True)
    technician_name: Mapped[str | None] = mapped_column(String(160), index=True)
    app_version: Mapped[str | None] = mapped_column(String(40), index=True)
    backend_version: Mapped[str | None] = mapped_column(String(40), index=True)
    install_mode: Mapped[str | None] = mapped_column(String(40), index=True)
    last_ip: Mapped[str | None] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="ativo", index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AIAuditEvent(Base):
    __tablename__ = "ai_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True, nullable=False)
    use_case: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    output_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    accepted: Mapped[bool | None] = mapped_column(Boolean)


class OperationalTemplate(Base):
    __tablename__ = "operational_templates"
    __table_args__ = (
        UniqueConstraint("document_type", "category", "label_key", name="uq_operational_template_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="", index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(140), nullable=False)
    label_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
