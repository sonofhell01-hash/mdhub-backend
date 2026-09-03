import base64
import json
import re
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.services.midiasimples.client import MidiaSimplesSession, extract_csrf


DEBUG_DIR = Path("data/runtime/debug_midiasimples")
DEFAULT_ARKLOK_MANAGER_ID = "148"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
    "application/pdf",
}


class LaudoSendError(RuntimeError):
    pass


def _is_login_response(final_url: str, body: str) -> bool:
    return MidiaSimplesSession().is_login_response(final_url, body)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _bool01(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    text = _text(value).lower()
    return "1" if text in {"1", "sim", "s", "true", "uso_inadequado"} else "0"


def _compact(text: str, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _save_debug(prefix: str, body: str) -> str:
    # Em serverless (Vercel) o pacote implantado fica em /var/task, que e
    # somente-leitura: tentar criar DEBUG_DIR ali derruba a requisicao com
    # FileNotFoundError mesmo quando o envio real ao MidiaSimples ja deu
    # certo. Tenta o diretorio configurado e cai para /tmp (unico gravavel
    # em serverless); se nada disso funcionar, nao deixa o debug HTML
    # derrubar o fluxo de envio - so devolve um aviso.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{stamp}_{prefix}.html"
    for directory in (DEBUG_DIR, Path("/tmp/mdhub_debug_midiasimples")):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            path.write_text(body or "", encoding="utf-8", errors="replace")
            return str(path)
        except OSError:
            continue
    return "(debug indisponivel: nenhum diretorio gravavel encontrado)"


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


def _action_to_path(action: str, base_url: str) -> str:
    action = _text(action)
    base = base_url.rstrip("/")
    if action.startswith(base):
        action = action[len(base):]
    if action.startswith("http://") or action.startswith("https://"):
        parsed = urllib.parse.urlparse(action)
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return action if action.startswith("/") else f"/{action}"


def _attrs_to_dict(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, _quote, value in re.findall(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", raw_attrs or "", re.S):
        attrs[key.lower()] = value
    return attrs


def _extract_post_form_action(html: str, base_url: str) -> str:
    fallback = ""
    for form_html in re.findall(r"<form\b[^>]*>.*?</form>", html or "", flags=re.I | re.S):
        form_open = re.search(r"<form\b([^>]*)>", form_html, flags=re.I | re.S)
        attrs = _attrs_to_dict(form_open.group(1) if form_open else "")
        method = (attrs.get("method") or "GET").upper()
        action = attrs.get("action") or ""
        if method != "POST" or not action:
            continue
        path = _action_to_path(action, base_url)
        lowered_path = path.lower()
        if lowered_path.endswith("/logout") or "/logout" in lowered_path:
            continue
        if "docusign" in lowered_path or "_method" in form_html:
            continue

        field_names = {
            match.group(1)
            for match in re.finditer(r"\bname=[\"']([^\"']+)[\"']", form_html, flags=re.I)
        }
        if {"col_id", "ticket", "equip_serial"}.issubset(field_names):
            return path
        if not fallback and "laudo-tecnico-tim" in lowered_path:
            fallback = path
    return fallback or "/laudo-tecnico-tim"


def _get_form(session: MidiaSimplesSession) -> tuple[str, str]:
    status, final_url, _headers, html = session.request(
        "GET",
        "/laudo-tecnico-tim/criar",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{session.base_url}/laudo-tecnico-tim",
        },
    )
    if status != 200:
        raise LaudoSendError(f"Falha ao abrir formulario de laudo: HTTP {status} ({final_url})")
    if _is_login_response(final_url, html):
        raise LaudoSendError("Sessao MidiaSimples expirada. Faca login novamente antes de enviar o laudo.")
    token = extract_csrf(html)
    if not token:
        debug = _save_debug("laudo_get_form_sem_csrf", html)
        raise LaudoSendError(f"CSRF do formulario de laudo nao encontrado. Debug: {debug}")
    action = _extract_post_form_action(html, session.base_url)
    return token, action


def _find_created_laudo(session: MidiaSimplesSession, ticket: str, laudo_id: str = "") -> dict[str, Any]:
    for search in [laudo_id, ticket, ""]:
        status, _final_url, _headers, body = session.request(
            "GET",
            f"/laudo-tecnico-tim?{_datatable_params(25, search or '')}",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{session.base_url}/laudo-tecnico-tim",
            },
        )
        if status != 200:
            continue
        try:
            payload = json.loads(body)
        except Exception:
            continue
        rows = payload.get("data") or []
        if laudo_id:
            id_rows = [row for row in rows if str(row.get("id") or "") == str(laudo_id)]
            if id_rows:
                return {"matched_by": "id", "row": id_rows[0]}
        if ticket:
            ticket_rows = [row for row in rows if _upper(row.get("ticket")) == _upper(ticket)]
            if ticket_rows:
                return {"matched_by": "ticket", "row": ticket_rows[0]}
    return {"matched_by": None, "row": None}


def _build_payload(token: str, document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or document_payload.get("colaborador") or {}
    equipamento = dados.get("equipamento_atual") or {}
    tecnico = dados.get("tecnico") or {}
    laudo = dados.get("laudo") or {}
    textos = laudo.get("textos") or {}
    pecas = laudo.get("pecas_reaproveitaveis") or {}
    uso_inadequado = bool(laudo.get("uso_inadequado"))

    ticket = _upper(document_payload.get("numero_chamado") or "")
    gerente = laudo.get("gerente") or {}
    gerente_matricula = _text(gerente.get("matricula") or laudo.get("gerente_matricula"))
    gerente_nome = _text(gerente.get("nome") or laudo.get("gerente_nome"))
    gerente_email = _text(gerente.get("email") or laudo.get("gerente_email"))

    model = _text(equipamento.get("modelo"))
    brand = _text(equipamento.get("marca"))
    peca_trocada = _text(textos.get("peca_trocada")).replace("{marca}", brand).replace("{modelo}", model)

    return {
        "_token": token,
        "col_id": _text(colaborador.get("matricula")),
        "col_regional": _text(colaborador.get("regional") or "CEO"),
        "col_full_name": _text(colaborador.get("nome")),
        "col_email": _upper(colaborador.get("email")) if uso_inadequado else "",
        "mang_id": gerente_matricula,
        "mang_name": gerente_nome,
        "mang_email": _upper(gerente_email) if uso_inadequado else "",
        "ticket": ticket,
        "ticket_date": date.today().isoformat(),
        "equip_type": _text(equipamento.get("categoria") or "NOTEBOOK"),
        "equip_host": _upper(equipamento.get("hostname")),
        "equip_brand": brand,
        "equip_model": model,
        "equip_serial": _upper(equipamento.get("serial")),
        "actions_before": _text(textos.get("acoes_executadas")),
        "defect_detected": _text(textos.get("defeito_detectado")),
        "case1": "1",
        "case2": "0",
        "case3": "1" if uso_inadequado else "0",
        "case4": "0" if uso_inadequado else "1",
        "case5": "1" if uso_inadequado else "0",
        "case6": "",
        "conc1": "1" if laudo.get("condicao_reparo") == "reparavel" else "0",
        "conc_description": _text(textos.get("descricao_analise")),
        "soluction": _text(textos.get("solucao")),
        "display": _bool01(pecas.get("display")),
        "keyboard": _bool01(pecas.get("teclado")),
        "batery": _bool01(pecas.get("bateria")),
        "chest": _bool01(pecas.get("carcaca")),
        "disck": _bool01(pecas.get("hd")),
        "memory": _bool01(pecas.get("memoria")),
        "description_change": peca_trocada,
        "responsible_id": _text(tecnico.get("midiasimples_id")),
        "manager_id": DEFAULT_ARKLOK_MANAGER_ID,
        "emission_date": date.today().isoformat(),
    }


def _decode_data_url(raw: str) -> tuple[str, bytes]:
    if not raw:
        return "", b""
    if raw.startswith("data:") and "," in raw:
        header, data = raw.split(",", 1)
        content_type = header.split(";", 1)[0].replace("data:", "").strip()
        return content_type, base64.b64decode(data)
    return "", base64.b64decode(raw)


def _extract_image_files(document_payload: dict[str, Any]) -> list[dict[str, Any]]:
    dados = document_payload.get("dados") or {}
    laudo = dados.get("laudo") or {}
    imagens = laudo.get("imagens") or {}
    raw_files = imagens.get("files") or []
    files = []
    for index, item in enumerate(raw_files):
        filename = _text(item.get("name") or f"evidencia_{index + 1}.jpg")
        data_url = _text(item.get("data_url") or item.get("dataUrl") or item.get("base64"))
        declared_type = _text(item.get("type") or item.get("content_type"))
        if not data_url:
            continue
        try:
            detected_type, content = _decode_data_url(data_url)
        except Exception as exc:
            raise LaudoSendError(f"Imagem {filename} invalida/base64 corrompido.") from exc
        content_type = declared_type or detected_type or "application/octet-stream"
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise LaudoSendError(f"Imagem {filename} possui tipo nao permitido: {content_type}.")
        if len(content) > MAX_IMAGE_BYTES:
            raise LaudoSendError(f"Imagem {filename} excede 8 MB.")
        files.append(
            {
                "field_name": "images[]",
                "filename": filename,
                "content_type": content_type,
                "content": content,
            }
        )
    return files


def send_laudo(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    token, action = _get_form(session)
    payload = _build_payload(token, document_payload)
    image_files = _extract_image_files(document_payload)
    missing = [
        name
        for name in (
            "responsible_id",
            "col_id",
            "col_full_name",
            "mang_id",
            "mang_name",
            "ticket",
            "equip_type",
            "equip_brand",
            "equip_model",
            "equip_serial",
            "actions_before",
            "defect_detected",
            "conc_description",
            "soluction",
            "description_change",
        )
        if not _text(payload.get(name))
    ]
    if missing:
        raise LaudoSendError(f"Laudo sem campos obrigatorios para envio real: {', '.join(missing)}")
    if image_files:
        status, final_url, _headers, body = session.multipart_request(
            "POST",
            action,
            fields=payload,
            files=image_files,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{session.base_url}/laudo-tecnico-tim/criar",
            },
        )
    else:
        status, final_url, _headers, body = session.request(
            "POST",
            action,
            data=payload,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{session.base_url}/laudo-tecnico-tim/criar",
            },
        )
    debug_html = _save_debug("post_laudo", body)
    if _is_login_response(final_url, body):
        raise LaudoSendError(
            f"Sessao MidiaSimples expirada durante o envio do laudo. Endpoint: {action}. Final: {final_url}. Debug: {debug_html}"
        )
    laudo_id = ""
    match = re.search(r"/laudo-tecnico-tim/(\d+)(?:/editar)?", final_url or "")
    if not match:
        match = re.search(r"Editar Relat[oó]rio T[eé]cnico #(\d+)", body or "", re.I)
    if match:
        laudo_id = match.group(1)

    if status not in (200, 302):
        raise LaudoSendError(f"POST Laudo retornou HTTP {status}. Debug: {debug_html}. Resumo: {_compact(body)}")

    verification = _find_created_laudo(session, payload["ticket"], laudo_id)
    row = verification.get("row")
    if not row and laudo_id:
        row = {"id": laudo_id, "ticket": payload["ticket"]}
        verification = {"matched_by": "edit_url", "row": row}
    if not row:
        raise LaudoSendError(
            f"POST Laudo respondeu HTTP {status}, mas nao consegui confirmar na listagem. Debug: {debug_html}"
        )
    confirmation = _confirm_images(session, str(row.get("id") or laudo_id or ""), image_files)

    return {
        "midiasimples_id": str(row.get("id") or laudo_id or ""),
        "final_url": final_url,
        "debug_html": debug_html,
        "matched_by": verification.get("matched_by"),
        "ticket": payload["ticket"],
        "images_sent": len(image_files),
        "images_confirmation": confirmation,
        "row": {key: value for key, value in row.items() if key != "action"},
    }


def _confirm_images(session: MidiaSimplesSession, laudo_id: str, image_files: list[dict[str, Any]]) -> dict[str, Any]:
    if not laudo_id:
        return {"status": "unknown", "message": "Laudo criado, mas sem ID para confirmar imagens."}
    status, final_url, _headers, body = session.request(
        "GET",
        f"/laudo-tecnico-tim/{laudo_id}/editar",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{session.base_url}/laudo-tecnico-tim",
        },
    )
    debug_html = _save_debug(f"laudo_{laudo_id}_confirm_images", body)
    if status != 200:
        return {"status": "unknown", "http_status": status, "final_url": final_url, "debug_html": debug_html}
    filenames = [str(item.get("filename") or "") for item in image_files]
    matched_names = [name for name in filenames if name and name in body]
    lower_body = body.lower()
    likely_uploaded = bool(matched_names) or ("* ao atualizar as imagens" in lower_body and ("storage" in lower_body or "/laudos" in lower_body or "img" in lower_body))
    return {
        "status": "confirmed" if likely_uploaded else "not_confirmed",
        "debug_html": debug_html,
        "matched_filenames": matched_names,
        "expected": len(image_files),
    }
