import re
from typing import Any, Literal
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Integer, case, cast
from sqlalchemy.orm import Session

from src.core.db_session import get_db
from src.models.core import AuditLog, Collaborator, Document, Equipment, SyncPending, WhatsAppHistory, WhatsAppQueue
from src.services.midiasimples.asset_senders import (
    AssetDocumentSendError,
    send_concessao,
    send_devolucao,
    send_emprestimo,
    send_substituicao,
    send_substituicao_headset,
)
from src.services.midiasimples.laudo_sender import LaudoSendError, send_laudo
from src.services.midiasimples.rat_sender import MidiaSimplesSendError, send_rat
from src.services.midiasimples.session_store import get_session, get_validated_session, invalidate_session
from src.services.inventory.normalizer import DataNormalizer
from src.services.noc.user_resolution import resolve_document_owner
from src.services.sync_queue import queue_event


router = APIRouter(prefix="/documents", tags=["Documentos Operacionais"])

DocumentType = Literal["rat", "laudo", "devolucao", "substituicao", "substituicao_headset", "concessao", "emprestimo", "rollout", "reposicao", "fechamento"]

SYNC_TYPE_BY_DOCUMENT = {
    "rat": "RAT_CREATED",
    "laudo": "LAUDO_CREATED",
    "devolucao": "DEVOLUCAO_CREATED",
    "substituicao": "SUBSTITUICAO_CREATED",
    "substituicao_headset": "SUBSTITUICAO_HEADSET_CREATED",
    "concessao": "CONCESSAO_CREATED",
    "emprestimo": "EMPRESTIMO_CREATED",
    "rollout": "ROLLOUT_CREATED",
    "reposicao": "REPOSICAO_CREATED",
    "fechamento": "FECHAMENTO_CREATED",
}


class CollaboratorSnapshot(BaseModel):
    matricula: str | None = None
    nome: str | None = None
    email: str | None = None
    telefone: str | None = None
    cargo: str | None = None
    regional: str | None = None


class DocumentDraftRequest(BaseModel):
    tipo: DocumentType
    numero_chamado: str | None = None
    usuario_id: int | None = None
    colaborador: CollaboratorSnapshot | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    queue_sync: bool = True
    status: Literal["pronto_envio"] = "pronto_envio"


class DocumentUpdateRequest(BaseModel):
    numero_chamado: str | None = None
    status: Literal["pronto_envio"] | None = None
    observacao: str | None = None
    payload: dict[str, Any] | None = None


class MidiaSimplesRatSyncRequest(BaseModel):
    email: str
    max_pages: int = Field(default=2, ge=1, le=200)
    page_size: int = Field(default=100, ge=10, le=100)
    clear_local_rats_without_midiasimples_id: bool = False
    include_all_technicians: bool = True


class DocumentDraftResponse(BaseModel):
    status: str
    document_id: int
    sync_id: int | None = None


class DocumentSendResponse(BaseModel):
    status: str
    document_id: int
    message: str


class DocumentValidationResponse(BaseModel):
    status: Literal["ready", "blocked"]
    document_id: int
    message: str
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FechamentoRatCandidate(BaseModel):
    document_id: int
    midiasimples_id: str | None = None
    numero_chamado: str | None = None
    created_at: str | None = None
    status: str
    responsavel: str
    colaborador: dict[str, Any] = Field(default_factory=dict)
    rat: dict[str, Any] = Field(default_factory=dict)
    suggested_script: str


class FechamentoRatCandidatesResponse(BaseModel):
    items: list[FechamentoRatCandidate]
    allowed_technicians: list[str]


ENABLED_SEND_TYPES = {"rat", "laudo", "devolucao", "substituicao", "substituicao_headset", "concessao", "emprestimo", "rollout"}
SCRIPT_ONLY_TYPES = {"fechamento", "reposicao"}

SIGNED_STATUS_KEYS = {
    "ASSINADO",
    "ASSINADA",
    "ASSINADA POR COMPLETA",
    "ASSINADO POR COMPLETA",
    "COMPLETED",
    "CONCLUIDO",
    "CONCLUIDA",
    "FINALIZADO",
    "FINALIZADA",
}

ALLOWED_RAT_TECHNICIANS = {
    "MARCEL DIEGO SILVA",
    "MARCOS PAULO DA SILVA REIS",
    "CAIO VINICIUS PEREIRA DA SILVA FREITAS",
    "MICHEL PURCINA DELOCCO",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _devolucao_personal_email(devolucao: dict[str, Any], colaborador: dict[str, Any]) -> str:
    return _text(devolucao.get("email_pessoal") or colaborador.get("email"))


def _normalize_upper(value: Any) -> str:
    import unicodedata

    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _extract_midiasimples_ticket(row: dict[str, Any]) -> str | None:
    import re

    value = _pick(row, "ticket", "numero_chamado", "call_number", "incident", "chamado")
    if value:
        return str(value).strip().upper()
    for key in ("action", "observations", "description", "descricao"):
        text = str(row.get(key) or "")
        match = re.search(r"(INC|REQ|LNR)\d{5,}", text, re.IGNORECASE)
        if match:
            return match.group(0).upper()
    return None


def _rat_responsible(row: dict[str, Any]) -> str:
    return _text(
        _pick(
            row,
            "arklok_responsible",
            "responsible",
            "responsavel_arklok",
            "responsavel",
            "analyst",
            "analista",
            "technician",
            "tecnico",
        )
    )


def _rat_signature_status(row: dict[str, Any]) -> str:
    return _text(
        _pick(
            row,
            "docusign_status",
            "status_docusign",
            "document_status",
            "signature_status",
            "status_assinatura",
            "status",
        )
    )


def _rat_is_signed(row: dict[str, Any]) -> bool:
    status = _normalize_upper(_rat_signature_status(row))
    if not status:
        return False
    return status in SIGNED_STATUS_KEYS or "COMPLET" in status or "ASSINAD" in status


def _rat_allowed_technician(row: dict[str, Any]) -> bool:
    responsible = _normalize_upper(_rat_responsible(row))
    if not responsible:
        return False
    return responsible in {_normalize_upper(name) for name in ALLOWED_RAT_TECHNICIANS}


def _repair_mojibake(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    # Alguns retornos do DataTables chegam como UTF-8 interpretado como Latin-1
    # (ex.: "substituiÃ§Ã£o"). Repara apenas quando esse padrao aparece.
    if "Ã" in text or "Â" in text:
        try:
            return text.encode("latin1").decode("utf-8")
        except UnicodeError:
            return text
    return text


def _short_analyst_name(full_name: str) -> str:
    normalized = _normalize_upper(full_name)
    if normalized == "MARCEL DIEGO SILVA":
        return "Marcel Silva"
    if normalized == "MICHEL PURCINA DELOCCO":
        return "Michel Delocco"
    if normalized == "MARCOS PAULO DA SILVA REIS":
        return "Marcos Reis"
    if normalized == "CAIO VINICIUS PEREIRA DA SILVA FREITAS":
        return "Caio Vinicius"
    words = [word.capitalize() for word in _text(full_name).split() if word]
    if len(words) >= 2:
        return f"{words[0]} {words[-1]}"
    return "Analista"


def _document_payload_areas(document: Document) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = document.payload or {}
    dados = payload.get("dados") if isinstance(payload.get("dados"), dict) else {}
    colaborador = dados.get("colaborador") if isinstance(dados.get("colaborador"), dict) else {}
    if not colaborador and isinstance(payload.get("colaborador"), dict):
        colaborador = payload.get("colaborador") or {}
    rat = dados.get("rat") if isinstance(dados.get("rat"), dict) else {}
    midiasimples = dados.get("midiasimples") if isinstance(dados.get("midiasimples"), dict) else {}
    return dados, colaborador, rat, midiasimples


def _normalize_closure_script_text(value: str) -> str:
    text = _repair_mojibake(value)
    replacements = {
        "Sintomas:#": "Sintomas:",
        "Analise / Diagnostico:#": "Análise / Diagnóstico:",
        "Análise / Diagnóstico:#": "Análise / Diagnóstico:",
        "Causa Raiz:": "Causa raiz:",
        "Acao(oes) executada(s):": "Ação(ões) executada(s):",
        "Resultado Obtido:": "Resultado obtido:",
        "Verificado Antivirus: (x)SIM ( )Nao": "Verificado Antivírus: (x) Sim ( ) Não",
        "Verificado Antivírus: (x)SIM ( )Não": "Verificado Antivírus: (x) Sim ( ) Não",
        "Verificado SCCM Client: (x)SIM ( )Nao": "Verificado SCCM Client: (x) Sim ( ) Não",
        "Verificado SCCM Client: (x)SIM ( )Não": "Verificado SCCM Client: (x) Sim ( ) Não",
        "Eliminacao de login administrador local irregular: ( )SIM (x)Nao Necessario": "Eliminação de login administrador local irregular: ( ) Sim (x) Não necessário",
        "Eliminação de login administrador local irregular: ( )SIM (x)Não Necessário": "Eliminação de login administrador local irregular: ( ) Sim (x) Não necessário",
        "Nome do Analista:": "Nome do analista:",
        "Horario de Chegada:": "Horário de chegada:",
        "Horário de Chegada:": "Horário de chegada:",
        "Horario de Saida:": "Horário de saída:",
        "Horário de Saída:": "Horário de saída:",
        "Horário de saída:19:00": "Horário de saída: 19:00",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.rstrip()
        if stripped.endswith("#"):
            stripped = stripped[:-1].rstrip()
        lines.append(stripped)
    return _normalize_ptbr_operational_text("\n".join(lines)).strip()


def _normalize_ptbr_operational_text(value: str) -> str:
    text = _repair_mojibake(value or "")
    replacements = [
        (r"\bnao\b", "não"),
        (r"\bapos\b", "após"),
        (r"\btecnico\b", "técnico"),
        (r"\btecnica\b", "técnica"),
        (r"\banalise\b", "análise"),
        (r"\bdiagnostico\b", "diagnóstico"),
        (r"\bacao\b", "ação"),
        (r"\bacoes\b", "ações"),
        (r"\bsubstituicao\b", "substituição"),
        (r"\bsolicitacao\b", "solicitação"),
        (r"\breposicao\b", "reposição"),
        (r"\bconfiguracao\b", "configuração"),
        (r"\breconfiguracao\b", "reconfiguração"),
        (r"\batualizacao\b", "atualização"),
        (r"\badequacao\b", "adequação"),
        (r"\binicializacao\b", "inicialização"),
        (r"\btemporaria\b", "temporária"),
        (r"\bfisica\b", "física"),
        (r"\bconexao\b", "conexão"),
        (r"\bconexoes\b", "conexões"),
        (r"\bmemoria\b", "memória"),
        (r"\bmaquina\b", "máquina"),
        (r"\bmae\b", "mãe"),
        (r"\busuario\b", "usuário"),
        (r"\btermica\b", "térmica"),
        (r"\bverificacao\b", "verificação"),
        (r"\bmanutencao\b", "manutenção"),
        (r"\bergonomico\b", "ergonômico"),
        (r"\bsubstituido\b", "substituído"),
        (r"\bsubstituida\b", "substituída"),
        (r"\bconcluido\b", "concluído"),
        (r"\bconcluida\b", "concluída"),
        (r"\bnecessario\b", "necessário"),
        (r"\bsaida\b", "saída"),
        (r"\bhorario\b", "horário"),
        (r"\bantivirus\b", "antivírus"),
        (r"\bcorrecao\b", "correção"),
        (r"\bavaliacao\b", "avaliação"),
        (r"\boperacao\b", "operação"),
        (r"\bobservacao\b", "observação"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _rat_document_responsible(document: Document) -> str:
    _dados, _colaborador, rat, midiasimples = _document_payload_areas(document)
    return _repair_mojibake(
        rat.get("responsible")
        or _rat_responsible(midiasimples)
        or (midiasimples.get("technician_name") if isinstance(midiasimples, dict) else "")
    )


def _rat_document_allowed(document: Document) -> bool:
    responsible = _normalize_upper(_rat_document_responsible(document))
    return bool(responsible) and responsible in {_normalize_upper(name) for name in ALLOWED_RAT_TECHNICIANS}


def _rat_document_haystack(document: Document, item: dict[str, Any]) -> str:
    parts: list[str] = [
        str(item.get("document_id") or ""),
        str(item.get("midiasimples_id") or ""),
        str(item.get("numero_chamado") or ""),
        str(item.get("responsavel") or ""),
    ]
    for section in ("colaborador", "rat"):
        section_value = item.get(section)
        if isinstance(section_value, dict):
            parts.extend(str(value) for value in section_value.values() if value not in (None, ""))
    return _normalize_upper(" ".join(parts))


_CLOSURE_LABELS = {
    "SINTOMAS": "sintomas",
    "ANALISE / DIAGNOSTICO": "diagnostico",
    "ANALISE/DIAGNOSTICO": "diagnostico",
    "ANALISE DIAGNOSTICO": "diagnostico",
    "CAUSA RAIZ": "causa",
    "ACAO(ES) EXECUTADA(S)": "acao",
    "ACAO(OES) EXECUTADA(S)": "acao",
    "ACAO (ES) EXECUTADA(S)": "acao",
    "ACAO (OES) EXECUTADA(S)": "acao",
    "ACOES EXECUTADAS": "acao",
    "ACAO EXECUTADA": "acao",
    "RESULTADO OBTIDO": "resultado",
    "MODELO": "modelo",
    "PATRIMONIO": "patrimonio",
    "S/N": "serial",
    "SN": "serial",
    "SERIAL": "serial",
    "USO EM DESACORDO COM O MANUAL?": "uso_manual",
    "USO EM DESACORDO COM O MANUAL": "uso_manual",
    "NOME DO ANALISTA": "analista",
    "HORARIO DE CHEGADA": "chegada",
    "HORARIO DE SAIDA": "saida",
}

_CLOSURE_IGNORED_LABELS = {
    "VERIFICADO ANTIVIRUS",
    "VERIFICADO SCCM CLIENT",
    "ELIMINACAO DE LOGIN ADMINISTRADOR LOCAL IRREGULAR",
    "ACOMPANHADO POR",
    "ATENCIOSAMENTE",
}


def _closure_label_from_line(line: str) -> tuple[str | None, str]:
    raw = _repair_mojibake(line).strip().strip("#").strip()
    if not raw:
        return None, ""
    if ":" in raw:
        label, value = raw.split(":", 1)
    else:
        label, value = raw, ""
    normalized = _normalize_upper(label).rstrip(".?").strip()
    if normalized in _CLOSURE_LABELS:
        return _CLOSURE_LABELS[normalized], value.strip()
    for known_label, known_key in _CLOSURE_LABELS.items():
        if normalized.startswith(f"{known_label} "):
            return known_key, raw[len(label) - len(normalized) + len(known_label) :].strip(" :.-")
    if normalized in _CLOSURE_IGNORED_LABELS:
        return "__ignore__", value.strip()
    for ignored_label in _CLOSURE_IGNORED_LABELS:
        if normalized.startswith(ignored_label):
            return "__ignore__", value.strip()
    return None, raw


def _cleanup_closure_value(value: Any) -> str:
    text = _normalize_ptbr_operational_text(value)
    cleaned_lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip().strip("#").strip()
        if not stripped:
            continue
        key, _rest = _closure_label_from_line(stripped)
        if key == "__ignore__":
            break
        cleaned_lines.append(stripped)
    return " ".join(" ".join(cleaned_lines).split()).strip()


def _extract_labeled_closure_fields(value: Any) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current_key: str | None = None
    for line in _normalize_closure_script_text(value).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key, rest = _closure_label_from_line(stripped)
        if key == "__ignore__":
            current_key = None
            continue
        if key:
            current_key = key
            if rest:
                fields.setdefault(key, []).append(rest)
            continue
        if current_key:
            fields.setdefault(current_key, []).append(rest)
    return {key: _cleanup_closure_value(" ".join(parts)) for key, parts in fields.items() if _cleanup_closure_value(" ".join(parts))}


def _equipment_field(equipment: dict[str, Any], *keys: str) -> str:
    return _cleanup_closure_value(_pick(equipment, *keys))


def _closure_yes_no(value: Any, default: str = "Não") -> str:
    text = _cleanup_closure_value(value)
    if not text:
        return default
    normalized = _normalize_upper(text)
    if normalized == "NAO":
        return "Não"
    if normalized == "SIM":
        return "Sim"
    return text


def _sanitize_rat_close_text(value: Any) -> str:
    raw = _repair_mojibake(value)
    parsed = _extract_labeled_closure_fields(raw)
    if parsed:
        return (
            parsed.get("acao")
            or parsed.get("resultado")
            or "Atendimento realizado conforme RAT."
        )
    return _cleanup_closure_value(raw)


def _sanitize_rat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload_copy = dict(payload or {})
    dados = dict(payload_copy.get("dados") or {})
    rat = dict(dados.get("rat") or {})
    textos = dict(rat.get("textos") or {})
    if "close_text" in textos:
        textos["close_text"] = _sanitize_rat_close_text(textos.get("close_text"))
        rat["textos"] = textos
        dados["rat"] = rat
        payload_copy["dados"] = dados
    return payload_copy


def _build_closure_script_from_rat(document: Document) -> str:
    dados, _colaborador, rat, midiasimples = _document_payload_areas(document)
    raw_close_text = (
        _pick(midiasimples, "close_text", "descricao_fechamento", "fechamento")
        or rat.get("close_text")
        or rat.get("fechamento")
        or ""
    )
    parsed = _extract_labeled_closure_fields(raw_close_text)

    problem = parsed.get("sintomas") or _cleanup_closure_value(
        _pick(midiasimples, "problem_text", "descricao_problema", "problem")
        or rat.get("problem_text")
        or rat.get("problema")
        or "Atendimento técnico realizado."
    )
    diagnosis = parsed.get("diagnostico") or _cleanup_closure_value(
        _pick(midiasimples, "other", "customer_other", "observations")
        or rat.get("other")
        or rat.get("diagnostico")
        or "Análise técnica"
    )
    if diagnosis.upper() in {"N/A", "-", "NONE", "NULL"}:
        diagnosis = "Análise técnica"

    close_text = _cleanup_closure_value(raw_close_text)
    action = parsed.get("acao") or close_text or "Atendimento realizado conforme RAT."
    cause = parsed.get("causa") or "Validado durante atendimento técnico."
    result_text = parsed.get("resultado") or "Equipamento em funcionamento normal."
    if not parsed.get("resultado") and ("LAUDO" in _normalize_upper(action) or "AGUARD" in _normalize_upper(action)):
        result_text = "Atendimento registrado conforme avaliação técnica."

    equipment = dados.get("equipamento_atual") if isinstance(dados.get("equipamento_atual"), dict) else {}
    if not equipment and isinstance(dados.get("equipamento"), dict):
        equipment = dados.get("equipamento") or {}
    analyst = parsed.get("analista") or _short_analyst_name(_rat_document_responsible(document))

    return "\n".join(
        [
            f"Sintomas: {problem}",
            f"Análise / Diagnóstico: {diagnosis}",
            f"Causa Raiz: {cause}",
            f"Ação (es) executada(s): {action}",
            f"Resultado Obtido: {result_text}",
            f"Modelo: {parsed.get('modelo') or _equipment_field(equipment, 'modelo', 'model')}",
            f"Patrimônio: {parsed.get('patrimonio') or _equipment_field(equipment, 'patrimonio', 'patrimony')}",
            f"S/n: {parsed.get('serial') or _equipment_field(equipment, 'serial', 'serial_number')}",
            f"Uso em desacordo com o manual? : {_closure_yes_no(parsed.get('uso_manual'))}",
            "Verificado Antivírus (x)SIM ( )",
            "Verificado SCCM Client. (x)SIM ( )Não",
            "Eliminação de login administrador local irregular ( )SIM (x)Não Necessário",
            f"Nome do Analista: {analyst}",
            "Horário de Chegada: 09:00",
            "Horário de Saída: 19:00",
        ]
    )


def _fechamento_rat_candidate(document: Document) -> dict[str, Any]:
    _dados, colaborador, rat, midiasimples = _document_payload_areas(document)
    midia_id = document.midiasimples_id or _text(rat.get("midiasimples_id") or _pick(midiasimples, "id")) or None
    numero_chamado = document.numero_chamado or _extract_midiasimples_ticket(midiasimples)
    return {
        "document_id": document.id,
        "midiasimples_id": midia_id,
        "numero_chamado": numero_chamado,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "status": document.status,
        "responsavel": _rat_document_responsible(document),
        "colaborador": {
            "matricula": _text(colaborador.get("matricula") or _pick(midiasimples, "customer_matriculation", "registration")).lstrip("F") or None,
            "nome": _repair_mojibake(colaborador.get("nome") or _pick(midiasimples, "customer_name", "name")),
            "email": _text(colaborador.get("email") or _pick(midiasimples, "customer_email", "email")) or None,
            "cargo": _repair_mojibake(colaborador.get("cargo") or _pick(midiasimples, "customer_role", "profile")),
        },
        "rat": {
            "problem_text": _repair_mojibake(_pick(midiasimples, "problem_text") or rat.get("problem_text")),
            "close_text": _repair_mojibake(_pick(midiasimples, "close_text") or rat.get("close_text")),
            "other": _repair_mojibake(_pick(midiasimples, "other") or rat.get("other")),
            "signature_status": _rat_signature_status(midiasimples) or _text(rat.get("signature_status")),
        },
        "suggested_script": _build_closure_script_from_rat(document),
    }


def _midiasimples_rat_status(row: dict[str, Any]) -> str:
    if _rat_is_signed(row):
        return "midiasimples_assinado"
    status = _normalize_upper(_rat_signature_status(row))
    if "ENVI" in status:
        return "midiasimples_enviado"
    return "midiasimples_sync"


def _parse_midiasimples_date(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _upsert_midiasimples_rat_document(db: Session, row: dict[str, Any]) -> tuple[Document, bool]:
    external_id = _text(_pick(row, "id"))
    if not external_id:
        raise ValueError("RAT sem ID externo do MidiaSimples.")

    matricula = _text(_pick(row, "registration", "customer_matriculation", "matricula", "registro")).lstrip("F")
    nome = _text(_pick(row, "name", "client_name", "customer_name", "nome", "colaborador")) or matricula or "Colaborador"
    email = _text(_pick(row, "email", "client_email", "customer_email")) or None
    cargo = _text(_pick(row, "profile", "cargo", "customer_role")) or None
    serial = _text(_pick(row, "serial", "product_serial_number")) or None
    created_at = _parse_midiasimples_date(_pick(row, "created_at", "created"))

    collaborator = None
    if matricula:
        collaborator = db.query(Collaborator).filter(Collaborator.matricula == matricula).first()
        if collaborator:
            collaborator.nome = nome or collaborator.nome
            collaborator.email = email or collaborator.email
            collaborator.cargo = cargo or collaborator.cargo
            collaborator.fonte = "midiasimples_rat"
        else:
            collaborator = Collaborator(
                matricula=matricula,
                nome=nome,
                email=email,
                cargo=cargo,
                fonte="midiasimples_rat",
            )
            db.add(collaborator)
            db.flush()

    payload = {
        "colaborador": {
            "matricula": matricula or None,
            "nome": nome,
            "email": email,
            "cargo": cargo,
        },
        "dados": {
            "colaborador": {
                "matricula": matricula or None,
                "nome": nome,
                "email": email,
                "cargo": cargo,
            },
            "tecnico": {"nome": _rat_responsible(row)},
            "equipamento_atual": {"serial": serial},
            "rat": {
                "signature_status": _rat_signature_status(row),
                "responsible": _rat_responsible(row),
                "midiasimples_id": external_id,
            },
            "midiasimples": row,
        },
    }

    document = (
        db.query(Document)
        .filter(Document.tipo == "rat", Document.midiasimples_id == external_id)
        .first()
    )
    created = False
    if not document:
        document = Document(
            tipo="rat",
            midiasimples_id=external_id,
            sync_pendente=False,
        )
        db.add(document)
        created = True

    document.colaborador_id = collaborator.id if collaborator else document.colaborador_id
    document.numero_chamado = _extract_midiasimples_ticket(row) or document.numero_chamado
    document.status = _midiasimples_rat_status(row)
    document.payload = payload
    document.response_payload = {"source": "midiasimples_sync", "row": row}
    if created_at and created:
        document.created_at = created_at
    if _rat_is_signed(row):
        document.enviado_em = document.enviado_em or created_at

    # Central NOC: resolve o responsavel real (nunca a equipe de quem rodou o
    # checker) para que a agregacao por equipe conte este RAT corretamente.
    # So resolve quando ainda nao ha usuario_id, para nunca sobrescrever uma
    # atribuicao ja feita/corrigida manualmente.
    if document.usuario_id is None:
        resolution = resolve_document_owner(db, row, fallback_name=_rat_responsible(row))
        if resolution.usuario_id is not None:
            document.usuario_id = resolution.usuario_id

    db.flush()
    return document, created


def _validate_document_ready(document: Document) -> DocumentValidationResponse:
    issues: list[str] = []
    warnings: list[str] = []

    payload = document.payload or {}
    dados = payload.get("dados") or {}

    if payload.get("_test_data") or dados.get("_test_data"):
        return DocumentValidationResponse(
            status="blocked",
            document_id=document.id,
            message="Dado ficticio de teste: envio real bloqueado.",
            issues=["Chamado marcado como teste e isolado do MidiaSimples."],
            warnings=["Use este registro somente para validar a interface."],
        )

    if document.tipo in SCRIPT_ONLY_TYPES:
        script_area = dados.get(document.tipo) or payload.get(document.tipo) or {}
        script_label = "Reposicao" if document.tipo == "reposicao" else "Fechamento"
        if not _text(script_area.get("texto") or script_area.get("script")):
            issues.append(f"{script_label} sem texto operacional.")
        warnings.append(f"{script_label} e script operacional para copiar e colar; nao entra em DocuSign ou WhatsApp.")
        if not issues:
            return DocumentValidationResponse(
                status="ready",
                document_id=document.id,
                message=f"Script de {script_label.lower()} pronto para copiar.",
                issues=[],
                warnings=warnings,
            )
        return DocumentValidationResponse(
            status="blocked",
            document_id=document.id,
            message=f"{script_label} precisa ter texto antes de copiar.",
            issues=issues,
            warnings=warnings,
        )

    if document.tipo not in ENABLED_SEND_TYPES:
        issues.append("Envio real ainda nao habilitado para este tipo.")

    if document.status != "pronto_envio":
        issues.append("Documento precisa estar como pronto_envio.")

    colaborador = dados.get("colaborador") or payload.get("colaborador") or {}
    tecnico = dados.get("tecnico") or {}
    equipamento_novo = dados.get("equipamento_novo") or {}
    headset_novo = dados.get("headset_novo") or {}
    devolucao = dados.get("devolucao") or {}
    laudo = dados.get("laudo") or {}
    rat = dados.get("rat") or {}

    technician_email = _text(tecnico.get("email"))
    if not technician_email:
        issues.append("Documento sem e-mail do tecnico.")
    elif not get_session(technician_email):
        issues.append("Sessao MidiaSimples do tecnico nao esta ativa. Faca login novamente.")

    if document.tipo in {"rat", "laudo", "substituicao", "substituicao_headset", "concessao", "emprestimo", "rollout"} and not _text(document.numero_chamado):
        issues.append("Numero do chamado e obrigatorio.")

    if document.tipo in {"rat", "laudo", "devolucao", "substituicao", "substituicao_headset", "concessao", "emprestimo", "rollout"}:
        for field, label in (
            ("matricula", "matricula do colaborador"),
            ("nome", "nome do colaborador"),
            ("email", "e-mail do colaborador"),
        ):
            if not _text(colaborador.get(field)):
                issues.append(f"Falta {label}.")

    if document.tipo == "rat":
        textos = (rat.get("textos") or {}) if isinstance(rat, dict) else {}
        atendimento = (rat.get("atendimento") or {}) if isinstance(rat, dict) else {}
        if not _text(atendimento.get("other")):
            issues.append("RAT sem campo Outros/tipo de atendimento.")
        if not _text(textos.get("problem_text")):
            issues.append("RAT sem sintoma/problema.")
        if not _text(textos.get("close_text")):
            issues.append("RAT sem fechamento.")

    if document.tipo == "laudo":
        gerente = (laudo.get("gerente") or {}) if isinstance(laudo, dict) else {}
        textos = (laudo.get("textos") or {}) if isinstance(laudo, dict) else {}
        imagens = (laudo.get("imagens") or {}) if isinstance(laudo, dict) else {}
        if not _text(gerente.get("matricula") or laudo.get("gerente_matricula")):
            issues.append("Laudo sem matricula do gerente TIM.")
        if not _text(gerente.get("nome") or laudo.get("gerente_nome")):
            issues.append("Laudo sem nome do gerente TIM.")
        if not (imagens.get("files") or []):
            warnings.append("Laudo sem imagens/evidencias anexadas. O envio seguira sem anexo.")
        equipamento = dados.get("equipamento_atual") or {}
        for key, label in (
            ("serial", "serial do equipamento"),
            ("marca", "marca do equipamento"),
            ("modelo", "modelo do equipamento"),
            ("hostname", "hostname do equipamento"),
        ):
            if not _text(equipamento.get(key)):
                issues.append(f"Laudo sem {label}. Complete manualmente antes de enviar.")
        for key, label in (
            ("acoes_executadas", "acoes executadas"),
            ("defeito_detectado", "defeito detectado"),
            ("descricao_analise", "descricao da analise"),
            ("solucao", "solucao"),
        ):
            if not _text(textos.get(key)):
                issues.append(f"Laudo sem {label}.")

    if document.tipo == "devolucao":
        if not _devolucao_personal_email(devolucao, colaborador):
            issues.append("Devolucao exige e-mail pessoal do colaborador.")
        warnings.append("No envio real, o sistema buscara a concessao mais recente no MidiaSimples antes de criar a devolucao.")

    if document.tipo == "substituicao_headset":
        if not _text(headset_novo.get("modelo") or headset_novo.get("model")):
            issues.append("Falta modelo do headset novo.")
        if not _text(headset_novo.get("serial")):
            issues.append("Falta serial do headset novo.")
        warnings.append("No envio real, o sistema criara apenas uma nova concessao, mantendo maquina e perifericos do termo atual e trocando somente o headset.")

    if document.tipo in {"substituicao", "concessao", "rollout"}:
        for field, label in (
            ("serial", "serial da maquina nova"),
            ("marca", "marca da maquina nova"),
        ):
            if not _text(equipamento_novo.get(field)):
                issues.append(f"Falta {label}.")
        if not _text(equipamento_novo.get("profile") or equipamento_novo.get("perfil")):
            issues.append("Falta perfil/kit da maquina nova.")
        if document.tipo in {"substituicao", "rollout"}:
            warnings.append("No envio real, o sistema criara colaborador, devolucao da maquina atual e concessao da maquina nova.")
        else:
            warnings.append("No envio real, o sistema criara colaborador e concessao da maquina nova.")

    if document.tipo == "emprestimo":
        emprestimo = dados.get("emprestimo") or {}
        for field, label in (
            ("serial", "serial do equipamento emprestado"),
            ("marca", "marca do equipamento emprestado"),
            ("modelo", "modelo do equipamento emprestado"),
        ):
            if not _text(equipamento_novo.get(field)):
                issues.append(f"Falta {label}.")
        if not _text(emprestimo.get("return_date")):
            warnings.append("Sem data prevista informada; o sistema usara 30 dias a partir de hoje no envio real.")
        warnings.append("No envio real, o sistema criara termo de emprestimo em /loan-term.")

    if issues:
        return DocumentValidationResponse(
            status="blocked",
            document_id=document.id,
            message="Documento ainda nao esta pronto para envio real.",
            issues=issues,
            warnings=warnings,
        )

    return DocumentValidationResponse(
        status="ready",
        document_id=document.id,
        message="Documento validado para envio real controlado.",
        issues=[],
        warnings=warnings,
    )


def _upsert_collaborator(db: Session, snapshot: CollaboratorSnapshot | None) -> Collaborator | None:
    if not snapshot or not snapshot.matricula:
        return None

    matricula = str(snapshot.matricula).strip()
    telefone = DataNormalizer.normalize_phone(snapshot.telefone)
    collaborator = db.query(Collaborator).filter(Collaborator.matricula == matricula).first()
    if collaborator:
        collaborator.nome = snapshot.nome or collaborator.nome
        collaborator.email = snapshot.email or collaborator.email
        collaborator.telefone = telefone or collaborator.telefone
        collaborator.cargo = snapshot.cargo or collaborator.cargo
        collaborator.regional = snapshot.regional or collaborator.regional
        collaborator.fonte = "documento_operacional"
        return collaborator

    collaborator = Collaborator(
        matricula=matricula,
        nome=snapshot.nome or matricula,
        email=snapshot.email,
        telefone=telefone,
        cargo=snapshot.cargo,
        regional=snapshot.regional,
        fonte="documento_operacional",
    )
    db.add(collaborator)
    db.flush()
    return collaborator


def _payload_map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _manual_collaborator_snapshot(payload: dict[str, Any]) -> CollaboratorSnapshot | None:
    dados = _payload_map(payload.get("dados"))
    colaborador = _payload_map(dados.get("colaborador") or payload.get("colaborador"))
    matricula = _text(colaborador.get("matricula") or dados.get("matricula"))
    if not matricula:
        return None
    return CollaboratorSnapshot(
        matricula=matricula,
        nome=_text(colaborador.get("nome") or dados.get("nome")) or matricula,
        email=_text(colaborador.get("email") or dados.get("email")) or None,
        telefone=_text(colaborador.get("telefone") or dados.get("telefone")) or None,
        cargo=_text(colaborador.get("cargo") or dados.get("cargo")) or None,
        regional=_text(colaborador.get("regional") or dados.get("regional") or "CEO") or None,
    )


def _upsert_manual_equipment(
    db: Session,
    collaborator: Collaborator | None,
    source: dict[str, Any],
    status: str,
    fonte: str,
) -> Equipment | None:
    serial = _text(source.get("serial")).upper()
    if not serial:
        return None
    equipment = db.query(Equipment).filter(Equipment.serial == serial).first()
    if not equipment:
        equipment = Equipment(serial=serial, fonte=fonte, status=status)
        db.add(equipment)
    equipment.colaborador_id = collaborator.id if collaborator else equipment.colaborador_id
    equipment.patrimonio = _text(source.get("patrimonio") or source.get("patrimony")) or equipment.patrimonio
    equipment.hostname = _text(source.get("hostname")).upper() or equipment.hostname
    equipment.categoria = _text(source.get("categoria") or source.get("tipo") or "NOTEBOOK") or equipment.categoria
    equipment.marca = _text(source.get("marca") or source.get("brand")) or equipment.marca
    equipment.modelo = _text(source.get("modelo") or source.get("model")) or equipment.modelo
    equipment.nota_fiscal = _text(source.get("nota_fiscal") or source.get("nf")) or equipment.nota_fiscal
    equipment.payload = {**(equipment.payload or {}), "manual_document_snapshot": source}
    equipment.fonte = fonte
    return equipment


def _persist_manual_payload_snapshot(db: Session, document: Document) -> None:
    payload = _payload_map(document.payload)
    snapshot = _manual_collaborator_snapshot(payload)
    collaborator = _upsert_collaborator(db, snapshot)
    if collaborator:
        document.colaborador_id = collaborator.id

    dados = _payload_map(payload.get("dados"))
    current_equipment = _payload_map(dados.get("equipamento_atual") or dados.get("equipamento") or payload.get("equipamento_atual") or payload.get("equipamento"))
    new_equipment = _payload_map(dados.get("equipamento_novo") or payload.get("equipamento_novo"))
    _upsert_manual_equipment(db, collaborator, current_equipment, "ativo", "documento_operacional_manual")
    if new_equipment:
        _upsert_manual_equipment(db, collaborator, new_equipment, "ativo", "documento_operacional_manual")
    db.flush()


def _create_document(db: Session, body: DocumentDraftRequest) -> tuple[Document, int | None]:
    collaborator = _upsert_collaborator(db, body.colaborador)
    is_script_only = body.tipo in SCRIPT_ONLY_TYPES
    document_status = "script_pronto" if is_script_only else "pronto_envio"
    dados_payload = body.payload
    if body.tipo == "rat":
        dados_payload = _sanitize_rat_payload({"dados": body.payload}).get("dados") or body.payload
    document = Document(
        tipo=body.tipo,
        colaborador_id=collaborator.id if collaborator else None,
        usuario_id=body.usuario_id,
        numero_chamado=body.numero_chamado,
        status=document_status,
        payload={
            "colaborador": body.colaborador.model_dump() if body.colaborador else None,
            "dados": dados_payload,
        },
        sync_pendente=False if is_script_only else body.queue_sync,
    )
    db.add(document)
    db.flush()
    _persist_manual_payload_snapshot(db, document)

    db.add(
        AuditLog(
            usuario_id=body.usuario_id,
            acao="DOCUMENT_DRAFT_CREATED",
            modulo=body.tipo,
            resultado=document_status,
            payload={"document_id": document.id, "numero_chamado": body.numero_chamado},
        )
    )

    sync_id = None
    if body.queue_sync and not is_script_only:
        sync = queue_event(
            db,
            tipo=SYNC_TYPE_BY_DOCUMENT[body.tipo],
            payload={
                "document_id": document.id,
                "tipo": body.tipo,
                "numero_chamado": body.numero_chamado,
                "payload": document.payload,
            },
            usuario_id=body.usuario_id,
        )
        sync_id = sync.id
    else:
        db.commit()

    db.refresh(document)
    return document, sync_id


@router.post("/draft", response_model=DocumentDraftResponse)
def create_draft(body: DocumentDraftRequest, db: Session = Depends(get_db)):
    document, sync_id = _create_document(db, body)
    return {"status": "ok", "document_id": document.id, "sync_id": sync_id}


@router.get("/")
def list_documents(
    tipo: DocumentType | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    # Documentos criados no desktop ainda nao tem midiasimples_id antes do envio.
    # Eles precisam aparecer antes do historico sincronizado do MidiaSimples para
    # o tecnico conseguir validar/enviar mesmo sem HUB central configurado.
    local_action_rank = case((Document.midiasimples_id.is_(None), 0), else_=1)
    query = db.query(Document).order_by(
        local_action_rank.asc(),
        Document.created_at.desc(),
        cast(Document.midiasimples_id, Integer).desc(),
        Document.id.desc(),
    )
    if tipo:
        query = query.filter(Document.tipo == tipo)
    if status:
        query = query.filter(Document.status == status)
    items = query.limit(limit).all()
    return {
        "items": [
            {
                "id": item.id,
                "tipo": item.tipo,
                "status": item.status,
                "numero_chamado": item.numero_chamado,
                "midiasimples_id": item.midiasimples_id,
                "sync_pendente": item.sync_pendente,
                "sync_tentativas": item.sync_tentativas,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "enviado_em": item.enviado_em.isoformat() if item.enviado_em else None,
                "payload": item.payload,
            }
            for item in items
        ]
    }


@router.post("/sync/midiasimples-rats")
def sync_midiasimples_rats(body: MidiaSimplesRatSyncRequest, db: Session = Depends(get_db)):
    stored = get_session(body.email)
    if not stored:
        raise HTTPException(status_code=401, detail="Sessao MidiaSimples nao esta ativa para este tecnico.")

    scanned = 0
    created = 0
    updated = 0
    skipped = 0
    out_of_scope = 0
    max_seen_id = 0
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if body.clear_local_rats_without_midiasimples_id:
        local_rat_ids = [
            document_id
            for document_id, in db.query(Document.id)
            .filter(Document.tipo == "rat", Document.midiasimples_id.is_(None))
            .all()
        ]
        for document_id in local_rat_ids:
            db.query(WhatsAppHistory).filter(WhatsAppHistory.documento_id == document_id).delete(synchronize_session=False)
            db.query(WhatsAppQueue).filter(WhatsAppQueue.documento_id == document_id).delete(synchronize_session=False)
            for sync in db.query(SyncPending).all():
                if _sync_payload_matches_document(sync.payload, document_id):
                    db.delete(sync)
            document = db.get(Document, document_id)
            if document:
                db.delete(document)

    for page in range(body.max_pages):
        start = page * body.page_size
        payload = stored.session.datatable(
            "/rat-attendance",
            search="",
            start=start,
            length=body.page_size,
            order_by_id_desc=True,
        )
        rows = payload.get("data") or []
        if not rows:
            break
        for row in rows:
            scanned += 1
            if not body.include_all_technicians and not _rat_allowed_technician(row):
                out_of_scope += 1
                continue
            try:
                document, was_created = _upsert_midiasimples_rat_document(db, row)
            except Exception as exc:
                skipped += 1
                errors.append({"id": _pick(row, "id"), "error": str(exc)})
                continue

            midia_id = int(document.midiasimples_id or 0) if str(document.midiasimples_id or "").isdigit() else 0
            max_seen_id = max(max_seen_id, midia_id)
            if was_created:
                created += 1
            else:
                updated += 1
            items.append(
                {
                    "document_id": document.id,
                    "midiasimples_id": document.midiasimples_id,
                    "numero_chamado": document.numero_chamado,
                    "status": document.status,
                    "responsavel": _rat_responsible(row),
                }
            )

    db.add(
        AuditLog(
            usuario_id=None,
            acao="MIDIASIMPLES_RATS_SYNCED",
            modulo="documents",
            resultado="ok",
            payload={
                "email": body.email,
                "scanned": scanned,
                "created": created,
                "updated": updated,
                "skipped": skipped,
                "out_of_scope": out_of_scope,
                "max_seen_midiasimples_id": max_seen_id,
                "cleared_local_without_midiasimples_id": body.clear_local_rats_without_midiasimples_id,
            },
        )
    )
    db.commit()
    return {
        "status": "ok",
        "module": "rats",
        "scanned": scanned,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "out_of_scope": out_of_scope,
        "max_seen_midiasimples_id": max_seen_id,
        "items": items[:100],
        "errors": errors[:50],
    }


@router.get("/fechamento/rats", response_model=FechamentoRatCandidatesResponse)
def list_fechamento_rats(
    q: str | None = Query(None, description="Busca por chamado, RAT, matricula, nome ou tecnico"),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Document)
        .filter(Document.tipo == "rat")
        .order_by(
            cast(Document.midiasimples_id, Integer).desc(),
            Document.created_at.desc(),
            Document.id.desc(),
        )
        .limit(500)
    )

    needle = _normalize_upper(q)
    items: list[dict[str, Any]] = []
    for document in query.all():
        if not _rat_document_allowed(document):
            continue
        item = _fechamento_rat_candidate(document)
        if needle and needle not in _rat_document_haystack(document, item):
            continue
        items.append(item)
        if len(items) >= limit:
            break

    return {
        "items": items,
        "allowed_technicians": sorted(ALLOWED_RAT_TECHNICIANS),
    }


@router.get("/{document_id}/validate-send", response_model=DocumentValidationResponse)
def validate_document_send(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Documento nao encontrado.")
    return _validate_document_ready(document)


@router.patch("/{document_id}")
def update_document(document_id: int, body: DocumentUpdateRequest, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Documento nao encontrado.")
    if document.status == "enviado" or document.midiasimples_id:
        raise HTTPException(status_code=409, detail="Documento ja enviado nao pode ser editado.")

    before = {
        "numero_chamado": document.numero_chamado,
        "status": document.status,
        "observacao": ((document.payload or {}).get("dados") or {}).get("observacao"),
        "payload": document.payload,
    }

    if body.numero_chamado is not None:
        document.numero_chamado = _text(body.numero_chamado).upper() or None
    if document.tipo in SCRIPT_ONLY_TYPES:
        document.status = "script_pronto"
        document.sync_pendente = False
    elif body.status is not None:
        document.status = body.status
    if body.observacao is not None:
        payload = dict(document.payload or {})
        dados = dict(payload.get("dados") or {})
        dados["observacao"] = body.observacao
        payload["dados"] = dados
        document.payload = payload
    if body.payload is not None:
        document.payload = _sanitize_rat_payload(body.payload) if document.tipo == "rat" else body.payload
        _persist_manual_payload_snapshot(db, document)

    db.add(
        AuditLog(
            usuario_id=document.usuario_id,
            acao="DOCUMENT_UPDATED",
            modulo=document.tipo,
            resultado="ok",
            payload={
                "document_id": document.id,
                "before": before,
                "after": {
                    "numero_chamado": document.numero_chamado,
                    "status": document.status,
                    "observacao": ((document.payload or {}).get("dados") or {}).get("observacao"),
                    "payload": document.payload,
                },
            },
        )
    )
    db.commit()
    db.refresh(document)
    return {
        "status": "ok",
        "document_id": document.id,
        "message": "Documento atualizado.",
    }


def _sync_payload_matches_document(payload: Any, document_id: int) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("document_id") == document_id:
        return True
    nested = payload.get("payload")
    return isinstance(nested, dict) and nested.get("document_id") == document_id


@router.delete("/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Documento nao encontrado.")
    if document.status == "enviado" or document.midiasimples_id:
        raise HTTPException(status_code=409, detail="Documento ja enviado nao pode ser excluido.")

    whatsapp_history_deleted = db.query(WhatsAppHistory).filter(WhatsAppHistory.documento_id == document.id).delete(synchronize_session=False)
    whatsapp_queue_deleted = db.query(WhatsAppQueue).filter(WhatsAppQueue.documento_id == document.id).delete(synchronize_session=False)

    sync_deleted = 0
    for sync in db.query(SyncPending).all():
        if _sync_payload_matches_document(sync.payload, document.id):
            db.delete(sync)
            sync_deleted += 1

    db.add(
        AuditLog(
            usuario_id=document.usuario_id,
            acao="DOCUMENT_DELETED",
            modulo=document.tipo,
            resultado="ok",
            payload={
                "document_id": document.id,
                "tipo": document.tipo,
                "numero_chamado": document.numero_chamado,
                "sync_deleted": sync_deleted,
                "whatsapp_queue_deleted": whatsapp_queue_deleted,
                "whatsapp_history_deleted": whatsapp_history_deleted,
            },
        )
    )
    db.delete(document)
    db.commit()
    return {
        "status": "deleted",
        "document_id": document_id,
        "message": "Documento excluido com filas relacionadas.",
        "deleted_sync": sync_deleted,
        "deleted_whatsapp_queue": whatsapp_queue_deleted,
        "deleted_whatsapp_history": whatsapp_history_deleted,
    }


@router.post("/{document_id}/send", response_model=DocumentSendResponse)
def send_document(document_id: int, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Documento nao encontrado.")

    payload = document.payload or {}
    dados = payload.get("dados") if isinstance(payload.get("dados"), dict) else {}
    if payload.get("_test_data") or dados.get("_test_data"):
        raise HTTPException(status_code=409, detail="Dado ficticio de teste: envio real bloqueado.")

    if document.tipo in SCRIPT_ONLY_TYPES:
        script_label = "Reposicao" if document.tipo == "reposicao" else "Fechamento de RAT"
        db.add(
            AuditLog(
                usuario_id=document.usuario_id,
                acao="DOCUMENT_SEND_BLOCKED",
                modulo=document.tipo,
                resultado="script_operacional",
                payload={"document_id": document.id, "tipo": document.tipo, "numero_chamado": document.numero_chamado},
                erro=f"{script_label} e script operacional para copiar e colar, nao documento para envio real.",
            )
        )
        db.commit()
        return {
            "status": "blocked",
            "document_id": document.id,
            "message": f"{script_label} nao e documento para envio. Use Copiar script e cole no chamado.",
        }

    document.sync_tentativas = (document.sync_tentativas or 0) + 1

    if document.tipo not in ENABLED_SEND_TYPES:
        db.add(
            AuditLog(
                usuario_id=document.usuario_id,
                acao="DOCUMENT_SEND_BLOCKED",
                modulo=document.tipo,
                resultado="modulo_nao_habilitado",
                payload={"document_id": document.id, "tipo": document.tipo, "numero_chamado": document.numero_chamado},
            erro="Envio real habilitado somente para RAT, Laudo, Devolucao, Substituicao, Sub/headset, Rollout, Concessao e Emprestimo nesta fase.",
            )
        )
        db.commit()
        return {
            "status": "blocked",
            "document_id": document.id,
            "message": "Envio real habilitado somente para RAT, Laudo, Devolucao, Sub/headset, Rollout, Concessao e Emprestimo nesta fase.",
        }

    if document.status != "pronto_envio":
        db.add(
            AuditLog(
                usuario_id=document.usuario_id,
                acao="DOCUMENT_SEND_BLOCKED",
                modulo=document.tipo,
                resultado="documento_nao_pronto",
                payload={"document_id": document.id, "status": document.status},
                erro="Documento precisa estar como pronto_envio para envio real.",
            )
        )
        db.commit()
        return {
            "status": "blocked",
            "document_id": document.id,
            "message": "Documento precisa estar como pronto_envio para envio real.",
        }

    validation = _validate_document_ready(document)
    if validation.status == "blocked":
        db.add(
            AuditLog(
                usuario_id=document.usuario_id,
                acao="DOCUMENT_SEND_BLOCKED",
                modulo=document.tipo,
                resultado="validacao_bloqueada",
                payload={
                    "document_id": document.id,
                    "tipo": document.tipo,
                    "numero_chamado": document.numero_chamado,
                    "issues": validation.issues,
                    "warnings": validation.warnings,
                },
                erro="; ".join(validation.issues),
            )
        )
        db.commit()
        message = validation.message
        if validation.issues:
            message = f"{message.rstrip('.')}: {validation.issues[0]}"
        return {
            "status": "blocked",
            "document_id": document.id,
            "message": message,
        }

    payload = document.payload or {}
    dados = payload.get("dados") or {}
    tecnico = dados.get("tecnico") or {}
    technician_email = tecnico.get("email")
    stored = get_validated_session(technician_email)
    if not stored:
        db.add(
            AuditLog(
                usuario_id=document.usuario_id,
                acao="DOCUMENT_SEND_BLOCKED",
                modulo=document.tipo,
                resultado="sem_sessao_midiasimples",
                payload={
                    "document_id": document.id,
                    "tipo": document.tipo,
                    "numero_chamado": document.numero_chamado,
                    "tecnico_email": technician_email,
                },
                erro="Envio real bloqueado: faca login MidiaSimples novamente para criar sessao no backend.",
            )
        )
        db.commit()
        return {
            "status": "blocked",
            "document_id": document.id,
            "message": "Faca login MidiaSimples novamente antes de enviar. A sessao atual do backend nao existe ou expirou.",
        }

    try:
        if document.tipo == "rat":
            result = send_rat(stored.session, {**payload, "numero_chamado": document.numero_chamado})
        elif document.tipo == "laudo":
            result = send_laudo(stored.session, {**payload, "numero_chamado": document.numero_chamado})
        elif document.tipo == "devolucao":
            result = send_devolucao(stored.session, {**payload, "numero_chamado": document.numero_chamado})
        elif document.tipo == "substituicao_headset":
            result = send_substituicao_headset(stored.session, {**payload, "numero_chamado": document.numero_chamado})
        elif document.tipo in {"substituicao", "rollout"}:
            result = send_substituicao(stored.session, {**payload, "numero_chamado": document.numero_chamado})
        elif document.tipo == "concessao":
            result = send_concessao(stored.session, {**payload, "numero_chamado": document.numero_chamado})
        else:
            result = send_emprestimo(stored.session, {**payload, "numero_chamado": document.numero_chamado})
    except (MidiaSimplesSendError, LaudoSendError, AssetDocumentSendError) as exc:
        if "Sessao MidiaSimples expirada" in str(exc):
            invalidate_session(technician_email)
        db.add(
            AuditLog(
                usuario_id=document.usuario_id,
                acao="DOCUMENT_SEND_ERROR",
                modulo=document.tipo,
                resultado="erro_midiasimples",
                payload={"document_id": document.id, "numero_chamado": document.numero_chamado},
                erro=str(exc),
            )
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    document.status = "enviado"
    document.midiasimples_id = result.get("midiasimples_id")
    document.response_payload = result
    document.enviado_em = datetime.now(timezone.utc)
    document.sync_pendente = False
    db.add(
        AuditLog(
            usuario_id=document.usuario_id,
            acao="DOCUMENT_SENT",
            modulo=document.tipo,
            resultado="ok",
            payload={
                "document_id": document.id,
                "numero_chamado": document.numero_chamado,
                "midiasimples_id": document.midiasimples_id,
            },
            resposta=result,
        )
    )
    db.commit()
    return {
        "status": "sent",
        "document_id": document.id,
        "message": f"{document.tipo.upper()} enviado ao MidiaSimples com ID {document.midiasimples_id}.",
    }


@router.post("/{tipo}/draft", response_model=DocumentDraftResponse)
def create_typed_draft(
    tipo: DocumentType,
    body: DocumentDraftRequest,
    db: Session = Depends(get_db),
):
    if body.tipo != tipo:
        raise HTTPException(status_code=400, detail="Tipo do path diferente do corpo da requisicao.")
    document, sync_id = _create_document(db, body)
    return {"status": "ok", "document_id": document.id, "sync_id": sync_id}
