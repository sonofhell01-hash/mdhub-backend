import json
import re
import unicodedata
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.services.midiasimples.client import MidiaSimplesSession, extract_csrf


DEBUG_DIR = Path("data/runtime/debug_midiasimples")


class MidiaSimplesSendError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _ascii_operational_text(value: Any) -> str:
    """Normaliza textos operacionais para evitar mojibake no MidiaSimples."""
    text = _text(value)
    if not text:
        return ""

    replacements = {
        "A├º├úo": "Acao",
        "A├º├úes": "Acoes",
        "AÃ§Ã£o": "Acao",
        "AÃ§Ãµes": "Acoes",
        "N├úo": "Nao",
        "NÃ£o": "Nao",
        "Necess├írio": "Necessario",
        "NecessÃ¡rio": "Necessario",
        "Antiv├¡rus": "Antivirus",
        "AntivÃ­rus": "Antivirus",
        "Elimina├º├úo": "Eliminacao",
        "EliminaÃ§Ã£o": "Eliminacao",
        "Hor├írio": "Horario",
        "HorÃ¡rio": "Horario",
        "Sa├¡da": "Saida",
        "SaÃ­da": "Saida",
        "DiagnÃ³stico": "Diagnostico",
        "Diagn├│stico": "Diagnostico",
    }
    for broken, fixed in replacements.items():
        text = text.replace(broken, fixed)

    try:
        if "Ã" in text or "Â" in text:
            text = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        pass

    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _compact(text: str, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _save_debug(prefix: str, body: str) -> str:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_DIR / f"{stamp}_{prefix}.html"
    path.write_text(body or "", encoding="utf-8", errors="replace")
    return str(path)


def _datatable_params(limit: int = 25, search: str = "") -> str:
    params = {
        "draw": "1",
        "start": "0",
        "length": str(limit),
        "search[value]": search,
        "search[regex]": "false",
        "order[0][column]": "0",
        "order[0][dir]": "desc",
    }
    for i, column in enumerate(("id", "created_at", "updated_at", "action")):
        params[f"columns[{i}][data]"] = column
        params[f"columns[{i}][name]"] = column
        params[f"columns[{i}][searchable]"] = "true" if column != "action" else "false"
        params[f"columns[{i}][orderable]"] = "true" if column != "action" else "false"
        params[f"columns[{i}][search][value]"] = ""
        params[f"columns[{i}][search][regex]"] = "false"
    return urllib.parse.urlencode(params)


def _get_create_token(session: MidiaSimplesSession) -> str:
    status, final_url, _headers, html = session.request(
        "GET",
        "/rat-attendance/criar",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Referer": f"{session.base_url}/rat-attendance",
        },
    )
    if status != 200:
        raise MidiaSimplesSendError(f"Falha ao abrir formulario de RAT: HTTP {status} ({final_url})")
    token = extract_csrf(html)
    if not token:
        debug = _save_debug("rat_get_form_sem_csrf", html)
        raise MidiaSimplesSendError(f"CSRF do formulario de RAT nao encontrado. Debug: {debug}")
    return token


def _find_created_rat(session: MidiaSimplesSession, ticket: str, rat_id: str = "") -> dict[str, Any]:
    for search in [rat_id, ticket, ""]:
        if search is None:
            continue
        status, _final_url, _headers, body = session.request(
            "GET",
            f"/rat-attendance?{_datatable_params(25, search)}",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{session.base_url}/rat-attendance",
            },
        )
        if status != 200:
            continue
        try:
            payload = json.loads(body)
        except Exception:
            continue
        rows = payload.get("data") or []
        if rat_id:
            id_rows = [row for row in rows if str(row.get("id") or "") == str(rat_id)]
            if id_rows:
                return {"matched_by": "id", "row": id_rows[0]}
        if ticket:
            ticket_rows = [
                row for row in rows
                if _upper(row.get("ticket")) == _upper(ticket)
            ]
            if ticket_rows:
                return {"matched_by": "ticket", "row": ticket_rows[0]}
    return {"matched_by": None, "row": None}


def _build_rat_payload(token: str, document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or document_payload.get("colaborador") or {}
    equipamento = dados.get("equipamento_atual") or {}
    tecnico = dados.get("tecnico") or {}
    rat = dados.get("rat") or {}
    atendimento = rat.get("atendimento") or {}
    textos = rat.get("textos") or {}
    horario = rat.get("horario") or {}
    validacoes = rat.get("validacoes") or {}

    ticket = _upper(document_payload.get("numero_chamado") or "")
    if not ticket:
        ticket = _upper(dados.get("numero_chamado") or "")

    return {
        "_token": token,
        "arklok_user_id": _text(tecnico.get("midiasimples_id")),
        "date": date.today().isoformat(),
        "start_time": _text(horario.get("start_time") or "09:00"),
        "end_time": _text(horario.get("end_time") or "19:00"),
        "customer_name": _text(colaborador.get("nome")),
        "customer_phone": _text(colaborador.get("telefone")),
        "customer_email": _upper(colaborador.get("email")),
        "customer_role": _text(colaborador.get("cargo")),
        "customer_departament": "",
        "customer_matriculation": _text(colaborador.get("matricula")),
        "customer_other": "",
        "ticket": ticket,
        "substituition": _text(atendimento.get("substituition") or "0"),
        "upgrade": _text(atendimento.get("upgrade") or "0"),
        "notebook": _text(atendimento.get("notebook") or "0"),
        "desktop": _text(atendimento.get("desktop") or "0"),
        "printer": _text(atendimento.get("printer") or "0"),
        "mobile": _text(atendimento.get("mobile") or "0"),
        "other": _text(atendimento.get("other")),
        "old_patrimony_number": "",
        "old_serial_number": "",
        "old_hostname": "",
        "old_customer_tag": "",
        "old_imei": "",
        "patrimony_number": _text(equipamento.get("patrimonio")),
        "serial_number": _upper(equipamento.get("serial")),
        "hostname": _upper(equipamento.get("hostname")),
        "customer_tag": "",
        "imei": "",
        "backup": _text(validacoes.get("backup") or "0"),
        "user_profile": _text(validacoes.get("user_profile")),
        "details1": _text(validacoes.get("details_1")),
        "details2": _text(validacoes.get("details_2")),
        "aim": _text(validacoes.get("aim") or "1"),
        "kace": _text(validacoes.get("kace") or "1"),
        "software_others": _text(validacoes.get("software_others")),
        "observations": _text(textos.get("observations")),
        "problem_text": _text(textos.get("problem_text")),
        "close_text": _ascii_operational_text(textos.get("close_text")),
    }


def send_rat(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    token = _get_create_token(session)
    payload = _build_rat_payload(token, document_payload)

    missing = [
        name
        for name in ("arklok_user_id", "customer_name", "customer_email", "customer_matriculation", "ticket", "other", "problem_text", "close_text")
        if not _text(payload.get(name))
    ]
    if missing:
        raise MidiaSimplesSendError(f"RAT sem campos obrigatorios para envio real: {', '.join(missing)}")

    status, final_url, _headers, body = session.request(
        "POST",
        "/rat-attendance",
        data=payload,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{session.base_url}/rat-attendance/criar",
        },
    )
    debug_html = _save_debug("post_rat", body)
    rat_id = ""
    match = re.search(r"/rat-attendance/(\d+)(?:/editar)?", final_url or "")
    if not match:
        match = re.search(r"(?:Editar|Cadastro).*?(?:RAT|Atendimento).*?#(\d+)", body or "", re.I | re.S)
    if match:
        rat_id = match.group(1)

    if status not in (200, 302):
        raise MidiaSimplesSendError(f"POST RAT retornou HTTP {status}. Debug: {debug_html}. Resumo: {_compact(body)}")

    verification = _find_created_rat(session, payload["ticket"], rat_id)
    row = verification.get("row")
    if not row:
        raise MidiaSimplesSendError(
            f"POST RAT respondeu HTTP {status}, mas nao consegui confirmar na listagem. Debug: {debug_html}"
        )

    return {
        "midiasimples_id": str(row.get("id") or rat_id or ""),
        "final_url": final_url,
        "debug_html": debug_html,
        "matched_by": verification.get("matched_by"),
        "ticket": payload["ticket"],
        "row": {key: value for key, value in row.items() if key != "action"},
    }
