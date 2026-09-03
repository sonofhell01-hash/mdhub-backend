import html
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.services.midiasimples.client import MidiaSimplesSession
from src.services.legacy.banco_clientes import buscar_equipamento_por_serial


DEBUG_DIR = Path("data/runtime/debug_midiasimples")

YES = "Sim"
NO = "Não"

PROFILE_DEFAULTS = {
    "PERFORMANCE": {
        "product_category": "Notebook",
        "product_model": "T14",
        "product_processor": "Intel® Core™ i5",
        "product_memory": "16GB DDR4",
        "product_disk": "SSD M.2 512GB",
        "product_price": "9.359,91",
    },
    "PERFORMANCE G4": {
        "product_category": "Notebook",
        "product_model": "T14 Gen4",
        "product_processor": "Intel® Core™ i5 13°Gen",
        "product_memory": "16GB DDR5",
        "product_disk": "SSD M.2 512GB",
        "product_price": "9.359,91",
    },
    "PERFORMANCE G6": {
        "product_category": "Notebook",
        "product_model": "T14Gen6",
        "product_processor": "Intel® Core™ i5 13°Gen",
        "product_memory": "16GB DDR5",
        "product_disk": "SSD M.2 512GB",
        "product_price": "9.359,91",
    },
    "5G": {
        "product_category": "Notebook",
        "product_model": "Latitude 5450",
        "product_processor": "Intel® Core™ i5",
        "product_memory": "16GB DDR5",
        "product_disk": "SSD M.2 512GB",
        "product_price": "9.359,91",
    },
}


class AssetDocumentSendError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


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


def _compact(text: str, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _attrs_to_dict(raw_attrs: str) -> dict[str, str]:
    result = {}
    for key, _quote, value in re.findall(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", raw_attrs or "", re.S):
        result[key.lower()] = html.unescape(value)
    return result


def _extract_forms(body: str) -> list[dict[str, Any]]:
    forms = []
    for form_html in re.findall(r"<form\b[^>]*>.*?</form>", body or "", flags=re.I | re.S):
        form_open = re.search(r"<form\b([^>]*)>", form_html, flags=re.I | re.S)
        form_attrs = _attrs_to_dict(form_open.group(1) if form_open else "")
        fields = []
        for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", form_html, flags=re.I | re.S):
            tag_name = re.match(r"<([a-zA-Z]+)", tag).group(1).lower()
            attrs = _attrs_to_dict(tag)
            name = attrs.get("name")
            if not name:
                continue
            fields.append(
                {
                    "tag": tag_name,
                    "name": name,
                    "type": attrs.get("type", ""),
                    "id": attrs.get("id", ""),
                    "value": attrs.get("value", ""),
                }
            )
        forms.append(
            {
                "method": (form_attrs.get("method") or "GET").upper(),
                "action": form_attrs.get("action") or "",
                "fields": fields,
            }
        )
    return forms


def _first_post_form(forms: list[dict[str, Any]]) -> dict[str, Any]:
    for form in forms:
        action = form.get("action") or ""
        if form.get("method") == "POST" and "logout" not in action:
            return form
    return {}


def _action_to_path(action: str, base_url: str) -> str:
    action = (action or "").strip()
    base = base_url.rstrip("/")
    if action.startswith(base):
        action = action[len(base):]
    if action.startswith("http://") or action.startswith("https://"):
        parsed = urllib.parse.urlparse(action)
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return action if action.startswith("/") else f"/{action}"


def _get_form(session: MidiaSimplesSession, path: str, referer: str, debug_name: str) -> dict[str, Any]:
    status, final_url, _headers, body = session.request(
        "GET",
        path,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{session.base_url}{referer}",
        },
    )
    _save_debug(f"get_{debug_name}", body)
    if status != 200:
        raise AssetDocumentSendError(f"Falha ao abrir {path}: HTTP {status} ({final_url})")
    form = _first_post_form(_extract_forms(body))
    if not form:
        raise AssetDocumentSendError(f"Nao encontrei formulario POST em {path}.")
    return form


def _submit_form(
    session: MidiaSimplesSession,
    form: dict[str, Any],
    payload: dict[str, Any],
    referer_path: str,
    debug_prefix: str,
) -> dict[str, Any]:
    path = _action_to_path(form.get("action") or "", session.base_url)
    status, final_url, headers, body = session.request(
        "POST",
        path,
        data=payload,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Referer": f"{session.base_url}{referer_path}",
        },
    )
    debug_html = _save_debug(debug_prefix, body)
    return {
        "status": status,
        "final_url": final_url,
        "content_type": headers.get("Content-Type"),
        "debug_html": debug_html,
        "body_preview": _compact(body, 1200),
    }


def _field_names(form: dict[str, Any]) -> set[str]:
    return {field.get("name") for field in (form.get("fields") or []) if field.get("name")}


def _token_from_form(form: dict[str, Any]) -> str:
    for field in form.get("fields") or []:
        if field.get("name") == "_token":
            return field.get("value") or ""
    return ""


def _field_values(form: dict[str, Any]) -> dict[str, str]:
    values = {}
    for field in form.get("fields") or []:
        name = field.get("name")
        if name and name not in values:
            values[name] = html.unescape(str(field.get("value") or "")).strip()
    return values


def _filter_payload(form: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    names = _field_names(form)
    return {key: value for key, value in payload.items() if key in names}


def _value(source: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = source.get(key)
        if raw not in (None, ""):
            return html.unescape(str(raw)).strip()
    return ""


def _norm_text(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _row_has(row: dict[str, Any], expected: str) -> bool:
    needle = _norm_text(expected)
    return bool(needle) and needle in _norm_text(json.dumps(row, ensure_ascii=False))


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


def _datatable_search(session: MidiaSimplesSession, path: str, search: str, limit: int = 25) -> dict[str, Any]:
    status, final_url, headers, body = session.request(
        "GET",
        f"{path}?{_datatable_params(limit, search)}",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{session.base_url}{path}",
        },
    )
    try:
        parsed = json.loads(body)
        rows = parsed.get("data") or []
    except Exception:
        rows = []
    return {
        "path": path,
        "search": search,
        "status": status,
        "final_url": final_url,
        "content_type": headers.get("Content-Type"),
        "rows": rows,
    }


def _get_tim_users_by_registration(session: MidiaSimplesSession, registration: str) -> list[dict[str, Any]]:
    status, _final_url, _headers, body = session.request(
        "GET",
        f"/api/tim-users?registration={urllib.parse.quote(str(registration))}",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    if status != 200:
        raise AssetDocumentSendError(f"Falha ao consultar colaborador {registration}: HTTP {status}")
    data = json.loads(body)
    rows = data.get("data") if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []


def _get_tim_user_detail(session: MidiaSimplesSession, user_id: int | str) -> dict[str, Any]:
    status, _final_url, _headers, body = session.request(
        "GET",
        f"/api/tim-users/{user_id}",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    if status != 200:
        raise AssetDocumentSendError(f"Falha ao consultar detalhe do colaborador {user_id}: HTTP {status}")
    data = json.loads(body)
    return data.get("data") or data


def _status_is_voided(status: str | None) -> bool:
    return _norm_text(status) in {"VOIDED", "CANCELADO", "CANCELADA", "CANCELED", "CANCELLED"}


def _select_current_concession(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = []
    fallback = []
    for row in rows:
        term = row.get("term_of_concession") or {}
        if not _value(term, "product_serial_number"):
            continue
        if _status_is_voided(row.get("docusign_status")):
            fallback.append(row)
        else:
            valid.append(row)
    candidates = valid or fallback
    candidates.sort(
        key=lambda row: (str(row.get("updated_at") or ""), str(row.get("created_at") or ""), int(row.get("id") or 0)),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _summarize_current(detail: dict[str, Any]) -> dict[str, Any]:
    term = detail.get("term_of_concession") or {}
    previous_devolution = detail.get("term_of_devolution") or {}
    return {
        "id": detail.get("id"),
        "name": _value(detail, "name") or _value(term, "customer_name"),
        "registration": _value(detail, "registration") or _value(term, "customer_registration"),
        "email": _value(detail, "email"),
        "cellphone": _value(detail, "cellphone"),
        "profile": _value(detail, "profile"),
        "role": _value(detail, "role") or _value(term, "customer_role"),
        "subsidiary": _value(detail, "subsidiary") or "CEO",
        "directory": _value(detail, "directory") or _value(previous_devolution, "directory") or "CEO",
        "term": term,
        "serial": _value(term, "product_serial_number") or _value(detail, "serial"),
    }


def _current_from_registration(session: MidiaSimplesSession, registration: str) -> tuple[dict[str, Any], set[int]]:
    rows = _get_tim_users_by_registration(session, registration)
    previous_ids = {int(row.get("id") or 0) for row in rows if row.get("id")}
    latest = _select_current_concession(rows)
    if not latest:
        raise AssetDocumentSendError(f"Nao encontrei termo de concessao ativo com serial para a matricula {registration}.")
    detail = _get_tim_user_detail(session, latest["id"])
    current = _summarize_current(detail)
    if not current.get("term"):
        raise AssetDocumentSendError("A API retornou colaborador, mas sem term_of_concession.")
    return current, previous_ids


def _normalize_profile(profile: str) -> str:
    return re.sub(r"\s+", " ", (profile or "").strip().upper()).replace("PERFORMACE", "PERFORMANCE")


def _profile_defaults(profile: str) -> dict[str, str]:
    return PROFILE_DEFAULTS.get(_normalize_profile(profile), PROFILE_DEFAULTS["PERFORMANCE"])


def _yes_from_term(value: Any, default: str = NO) -> str:
    text = html.unescape(str(value or "")).strip().lower()
    if not text:
        return default
    if text in {"sim", "s", "yes", "1", "true"}:
        return YES
    if text in {"não", "nao", "n", "no", "0", "false"}:
        return NO
    return default


def _has_concession(term: dict[str, Any], key: str, model_key: str = "") -> bool:
    if model_key and _value(term, model_key):
        return True
    return _yes_from_term(term.get(key), NO) == YES


def _return_value(conceded: bool, mode: str) -> str:
    if mode == "total":
        return YES if conceded else NO
    if mode == "equipamento":
        return NO
    return YES if conceded else NO


def _build_standalone_devolucao_payload(
    form: dict[str, Any],
    current: dict[str, Any],
    mode: str,
    personal_email: str,
    observation: str,
) -> dict[str, Any]:
    term = current.get("term") or {}
    payload = {name: "" for name in _field_names(form)}
    payload["_token"] = _token_from_form(form)

    mouse = _has_concession(term, "mouse_check")
    keyboard = _has_concession(term, "keyboard_check")
    monitor = _has_concession(term, "monitor_check", "monitor_serial_number")
    bag = _has_concession(term, "laptop_bag_check")
    headset = _has_concession(term, "headset_check", "headset_model")
    power = _has_concession(term, "power_adapter_check", "power_adapter_model")

    payload.update(
        {
            "client_name": current.get("name") or "",
            "directory": current.get("directory") or current.get("subsidiary") or "CEO",
            "registration": current.get("registration") or "",
            "client_email": personal_email,
            "product_category": _value(term, "product_category") or "NOTEBOOK",
            "product_brand": _value(term, "product_brand"),
            "product_model": _value(term, "product_model"),
            "product_serial_number": _value(term, "product_serial_number"),
            "mouse_concession": YES if mouse else NO,
            "mouse_check": _return_value(mouse, mode),
            "keyboard_concession": YES if keyboard else NO,
            "keyboard_check": _return_value(keyboard, mode),
            "monitor_concession": YES if monitor else NO,
            "monitor_chek": _return_value(monitor, mode),
            "monitor_model": _value(term, "monitor_model"),
            "monitor_serial_number": _value(term, "monitor_serial_number"),
            "laptop_bag_concession": YES if bag else NO,
            "laptop_bag_check": _return_value(bag, mode),
            "headset_concession": YES if headset else NO,
            "headset_check": _return_value(headset, mode),
            "headset_model": _value(term, "headset_model"),
            "power_adapter_concession": YES if power else NO,
            "power_adapter_check": _return_value(power, mode),
            "power_adapter_model": _value(term, "power_adapter_model"),
            "rca_cable_concession": _yes_from_term(term.get("rca_cable_check"), NO),
            "rca_cable_check": NO,
            "ergonomic_concession": _yes_from_term(term.get("ergonomic_check"), NO),
            "ergonomic_check": NO,
            "battery_concession": _yes_from_term(term.get("battery_check"), NO),
            "battery_check": NO,
            "safe_cable_concession": _yes_from_term(term.get("safe_cable_check"), NO),
            "safe_cable_check": NO,
            "charger_concession": _yes_from_term(term.get("charger_check"), NO),
            "charger_check": NO,
            "hdmi_adapter_concession": _yes_from_term(term.get("hdmi_adpter_chek"), NO),
            "hdmi_adapter_chek": NO,
            "dock_concession": YES if _value(term, "dock_serial") else _yes_from_term(term.get("dock_check"), NO),
            "dock_check": NO,
            "dock_model": _value(term, "dock_model"),
            "dock_serial": _value(term, "dock_serial"),
            "webcam_concession": YES if _value(term, "webcam_model") else _yes_from_term(term.get("webcam_check"), NO),
            "webcam_check": NO,
            "webcam_model": _value(term, "webcam_model"),
            "usb_hub_concession": YES if _value(term, "usb_hub_model") else _yes_from_term(term.get("usb_hub_check"), NO),
            "usb_hub_check": NO,
            "usb_hub_model": _value(term, "usb_hub_model"),
            "monitor_power_cable_concession": _yes_from_term(term.get("monitor_power_cable_check"), NO),
            "monitor_power_cable_check": NO,
            "monitor_power_cable_model": _value(term, "monitor_power_cable_model"),
            "others": observation,
        }
    )
    return _filter_payload(form, payload)


def _build_envelope_devolucao_payload(form: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    return _build_standalone_devolucao_payload(
        form,
        current,
        "equipamento",
        current.get("email") or "",
        "REALIZADA APENAS A SUBSTITUICAO DE MAQUINA, MANTENDO-SE OS PERIFERICOS ENTREGUES ANTERIORMENTE.",
    )


def _build_colaborador_payload(form: dict[str, Any], current: dict[str, Any], new_machine: dict[str, Any]) -> dict[str, Any]:
    payload = {name: "" for name in _field_names(form)}
    payload["_token"] = _token_from_form(form)
    payload.update(
        {
            "name": current.get("name") or "",
            "profile": new_machine.get("profile") or current.get("profile") or "PERFORMANCE",
            "email": current.get("email") or "",
            "registration": current.get("registration") or "",
            "cellphone": current.get("cellphone") or "",
            "scheduled_to": date.today().isoformat(),
            "time": new_machine.get("scheduled_time") or "17:00",
            "subsidiary": current.get("subsidiary") or "CEO",
            "role": current.get("role") or "",
        }
    )
    return _filter_payload(form, payload)


def _build_concessao_payload(form: dict[str, Any], current: dict[str, Any], new_machine: dict[str, Any]) -> dict[str, Any]:
    old_term = current.get("term") or {}
    defaults = _profile_defaults(new_machine.get("profile") or current.get("profile") or "")
    form_defaults = _field_values(form)
    headset_model = _text(new_machine.get("headset_model")) or _value(old_term, "headset_model")
    payload = {name: "" for name in _field_names(form)}
    payload["_token"] = _token_from_form(form)
    payload.update(
        {
            "customer_name": current.get("name") or "",
            "customer_role": current.get("role") or current.get("profile") or "",
            "customer_registration": current.get("registration") or "",
            "ticket": new_machine.get("ticket") or "",
            "product_category": form_defaults.get("product_category") or defaults["product_category"],
            "product_brand": new_machine.get("brand") or "LENOVO",
            "product_model": new_machine.get("model") or form_defaults.get("product_model") or defaults["product_model"],
            "product_serial_number": new_machine.get("serial") or "",
            "incoice_number": new_machine.get("nf") or "",
            "product_number": new_machine.get("patrimony") or "",
            "hostname": new_machine.get("hostname") or "",
            "product_processor": form_defaults.get("product_processor") or defaults["product_processor"],
            "product_memory": form_defaults.get("product_memory") or defaults["product_memory"],
            "product_disk": form_defaults.get("product_disk") or defaults["product_disk"],
            "product_price": form_defaults.get("product_price") or defaults["product_price"],
            "mouse_check": _yes_from_term(old_term.get("mouse_check"), YES),
            "keyboard_check": _yes_from_term(old_term.get("keyboard_check"), YES),
            "monitor_check": YES if _value(old_term, "monitor_serial_number") else _yes_from_term(old_term.get("monitor_check"), NO),
            "monitor_model": _value(old_term, "monitor_model"),
            "monitor_serial_number": _value(old_term, "monitor_serial_number"),
            "laptop_bag_check": _yes_from_term(old_term.get("laptop_bag_check"), YES),
            "ergonomic_check": _yes_from_term(old_term.get("ergonomic_check"), NO),
            "safe_cable_check": _yes_from_term(old_term.get("safe_cable_check"), NO),
            "headset_check": YES if headset_model else _yes_from_term(old_term.get("headset_check"), NO),
            "headset_model": headset_model,
            "power_adapter_check": _yes_from_term(old_term.get("power_adapter_check"), YES),
            "power_adapter_model": _value(old_term, "power_adapter_model"),
            "rca_cable_check": NO,
            "welcome_kit": NO,
            "battery_check": NO,
            "charger_check": _yes_from_term(old_term.get("charger_check"), NO),
            "hdmi_adpter_chek": NO,
            "dock_check": YES if _value(old_term, "dock_serial") else _yes_from_term(old_term.get("dock_check"), NO),
            "dock_model": _value(old_term, "dock_model"),
            "dock_serial": _value(old_term, "dock_serial"),
            "security_check": NO,
            "webcam_check": YES if _value(old_term, "webcam_model") else _yes_from_term(old_term.get("webcam_check"), NO),
            "webcam_model": _value(old_term, "webcam_model"),
            "usb_hub_check": YES if _value(old_term, "usb_hub_model") else _yes_from_term(old_term.get("usb_hub_check"), NO),
            "monitor_power_cable_check": _yes_from_term(old_term.get("monitor_power_cable_check"), NO),
            "responsible_name": new_machine.get("responsible_name") or "",
            "local": "Rio de Janeiro - RJ",
            "date": date.today().isoformat(),
            "delivered_at_store": NO,
            "observation": new_machine.get("observation") or "REALIZADA APENAS A SUBSTITUICAO DE MAQUINA.",
        }
    )
    return _filter_payload(form, payload)


def _find_new_collaborator_id(session: MidiaSimplesSession, registration: str, previous_ids: set[int], profile: str) -> int | None:
    rows = _get_tim_users_by_registration(session, registration)
    profile_key = _norm_text(profile)
    candidates = []
    for row in rows:
        row_id = int(row.get("id") or 0)
        if not row_id or row_id in previous_ids:
            continue
        weight = 1 if profile_key and _norm_text(row.get("profile")) == profile_key else 0
        candidates.append((weight, row_id))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _required(payload: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if not _text(payload.get(field))]
    if missing:
        raise AssetDocumentSendError(f"{label} sem campos obrigatorios: {', '.join(missing)}")


def _new_machine_from_payload(document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or {}
    equipamento_novo = dados.get("equipamento_novo") or {}
    tecnico = dados.get("tecnico") or {}
    profile = _text(equipamento_novo.get("profile") or equipamento_novo.get("perfil") or dados.get("profile") or dados.get("perfil") or colaborador.get("perfil") or "PERFORMANCE")
    defaults = _profile_defaults(profile)
    serial = _upper(equipamento_novo.get("serial"))
    asset = buscar_equipamento_por_serial(serial) or {}
    return {
        "ticket": _upper(document_payload.get("numero_chamado") or dados.get("numero_chamado")),
        "serial": serial,
        "brand": _text(equipamento_novo.get("marca") or equipamento_novo.get("brand") or asset.get("marca") or "LENOVO"),
        "model": _text(equipamento_novo.get("modelo") or equipamento_novo.get("model") or asset.get("modelo") or defaults["product_model"]),
        "profile": profile,
        "nf": _text(equipamento_novo.get("nota_fiscal") or equipamento_novo.get("nf") or asset.get("nota_fiscal")),
        "patrimony": _text(equipamento_novo.get("patrimonio") or equipamento_novo.get("patrimony") or asset.get("patrimonio")),
        "hostname": _upper(equipamento_novo.get("hostname") or asset.get("hostname") or f"NAKRJ{colaborador.get('matricula') or ''}"),
        "scheduled_time": _text(equipamento_novo.get("scheduled_time") or "17:00"),
        "responsible_name": _text(tecnico.get("full_name") or tecnico.get("display_name")),
        "observation": _text(equipamento_novo.get("observacao")),
    }


def _collaborator_from_payload(document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or document_payload.get("colaborador") or {}
    return {
        "id": None,
        "name": _text(colaborador.get("nome")),
        "registration": _text(colaborador.get("matricula")),
        "email": _text(colaborador.get("email")),
        "cellphone": _text(colaborador.get("telefone")),
        "profile": _text(dados.get("profile") or "PERFORMANCE"),
        "role": _text(colaborador.get("cargo")),
        "subsidiary": _text(colaborador.get("regional") or "CEO"),
        "directory": "CEO",
        "term": {},
        "serial": "",
    }


def _verify_devolucao(session: MidiaSimplesSession, current: dict[str, Any], ticket: str = "") -> dict[str, Any]:
    serial = _value(current.get("term") or {}, "product_serial_number")
    registration = current.get("registration") or ""
    checks = []
    checks.append(_datatable_search(session, "/termo-de-devolucao", "", limit=100))
    for search in [serial, registration, ticket]:
        if search:
            checks.append(_datatable_search(session, "/termo-de-devolucao", search))
    matches = []
    for check in checks:
        for row in check.get("rows") or []:
            if _row_has(row, serial) and (not registration or _row_has(row, registration)):
                matches.append(row)
    return {"found": bool(matches), "matches": matches[:5], "checks": [{"search": c["search"], "status": c["status"], "count": len(c["rows"])} for c in checks]}


def _verify_concessao(session: MidiaSimplesSession, current: dict[str, Any], new_machine: dict[str, Any], expected_id: int | None = None) -> dict[str, Any]:
    serial = new_machine.get("serial") or ""
    registration = current.get("registration") or ""
    ticket = new_machine.get("ticket") or ""
    checks = []
    for search in [serial, registration, ticket]:
        if search:
            checks.append(_datatable_search(session, "/colaboradores-tim", search))
    matches = []
    for check in checks:
        for row in check.get("rows") or []:
            if expected_id is not None:
                try:
                    row_id = int(row.get("id") or 0)
                except (TypeError, ValueError):
                    row_id = 0
                if row_id != expected_id:
                    continue
            if _row_has(row, serial) and (not registration or _row_has(row, registration)):
                matches.append(row)
    return {"found": bool(matches), "matches": matches[:5], "checks": [{"search": c["search"], "status": c["status"], "count": len(c["rows"])} for c in checks]}


def _loan_machine_from_payload(document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    equipamento = dados.get("equipamento_novo") or dados.get("equipamento_atual") or {}
    colaborador = dados.get("colaborador") or {}
    emprestimo = dados.get("emprestimo") or {}
    return {
        "ticket": _upper(document_payload.get("numero_chamado") or dados.get("numero_chamado")),
        "category": _text(equipamento.get("categoria") or equipamento.get("tipo") or emprestimo.get("tipo_equipamento") or "NOTEBOOK"),
        "brand": _text(equipamento.get("marca") or equipamento.get("brand") or "LENOVO"),
        "model": _text(equipamento.get("modelo") or equipamento.get("model")),
        "serial": _upper(equipamento.get("serial")),
        "patrimony": _text(equipamento.get("patrimonio") or equipamento.get("patrimony")),
        "given_at": _text(emprestimo.get("given_at") or date.today().isoformat()),
        "return_date": _text(emprestimo.get("return_date") or (date.today() + timedelta(days=30)).isoformat()),
        "local": _text(emprestimo.get("local") or "Rio de Janeiro - RJ"),
        "registration": _text(colaborador.get("matricula")),
    }


def _build_emprestimo_payload(form: dict[str, Any], document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or document_payload.get("colaborador") or {}
    tecnico = dados.get("tecnico") or {}
    machine = _loan_machine_from_payload(document_payload)
    form_defaults = _field_values(form)
    payload = {name: "" for name in _field_names(form)}
    payload["_token"] = _token_from_form(form)
    payload.update(
        {
            "arklok_user_id": _text(tecnico.get("midiasimples_id") or form_defaults.get("arklok_user_id")),
            "date": date.today().isoformat(),
            "customer_name": _text(colaborador.get("nome")),
            "customer_email": _text(colaborador.get("email")),
            "customer_matriculation": machine["registration"],
            "ticket": machine["ticket"],
            "equipament_type": machine["category"],
            "equipament_brand": machine["brand"],
            "equipament_model": machine["model"],
            "equipament_serial": machine["serial"],
            "equipament_patrimony": machine["patrimony"],
            "given_at": machine["given_at"],
            "return_date": machine["return_date"],
            "local": machine["local"],
        }
    )
    return _filter_payload(form, payload)


def _verify_emprestimo(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    machine = _loan_machine_from_payload(document_payload)
    checks = []
    for search in [machine["ticket"], machine["serial"], machine["registration"]]:
        if search:
            checks.append(_datatable_search(session, "/loan-term", search))
    matches = []
    for check in checks:
        for row in check.get("rows") or []:
            if _row_has(row, machine["serial"]) and (
                _row_has(row, machine["ticket"]) or _row_has(row, machine["registration"])
            ):
                matches.append(row)
    return {"found": bool(matches), "matches": matches[:5], "checks": [{"search": c["search"], "status": c["status"], "count": len(c["rows"])} for c in checks]}


def _create_concessao_for_current(
    session: MidiaSimplesSession,
    current: dict[str, Any],
    previous_ids: set[int],
    new_machine: dict[str, Any],
    flow: str,
    observation: str,
) -> dict[str, Any]:
    collaborator_form = _get_form(session, "/colaboradores-tim/criar", "/colaboradores-tim", f"{flow}_colaborador_criar")
    collaborator_payload = _build_colaborador_payload(collaborator_form, current, new_machine)
    collaborator_response = _submit_form(session, collaborator_form, collaborator_payload, "/colaboradores-tim/criar", f"post_{flow}_colaborador")
    if not (200 <= int(collaborator_response["status"]) < 400):
        raise AssetDocumentSendError(f"Falha ao criar colaborador: HTTP {collaborator_response['status']}. Debug: {collaborator_response['debug_html']}")

    new_id = _find_new_collaborator_id(session, current["registration"], previous_ids, new_machine["profile"])
    if not new_id:
        raise AssetDocumentSendError(f"Colaborador criado, mas nao encontrei novo ID para matricula {current['registration']}. Debug: {collaborator_response['debug_html']}")

    concession_form = _get_form(session, f"/colaboradores-tim/{new_id}/termo-concessao/criar", "/colaboradores-tim", f"{flow}_concessao_criar")
    concession_payload = _build_concessao_payload(concession_form, current, {**new_machine, "observation": observation})
    concession_response = _submit_form(
        session,
        concession_form,
        concession_payload,
        f"/colaboradores-tim/{new_id}/termo-concessao/criar",
        f"post_{flow}_concessao",
    )
    if not (200 <= int(concession_response["status"]) < 400):
        raise AssetDocumentSendError(f"Falha ao criar concessao: HTTP {concession_response['status']}. Debug: {concession_response['debug_html']}")
    verification = _verify_concessao(session, current, new_machine, new_id)
    if not verification.get("found"):
        raise AssetDocumentSendError(f"POST Concessao respondeu, mas nao confirmou na listagem. Debug: {concession_response['debug_html']}")
    row = verification["matches"][0]
    return {
        "midiasimples_id": str(row.get("id") or new_id),
        "colaborador_id": new_id,
        "colaborador_response": collaborator_response,
        "concession_response": concession_response,
        "verification": verification,
    }


def send_emprestimo(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    form = _get_form(session, "/loan-term/criar", "/loan-term", "emprestimo_criar")
    payload = _build_emprestimo_payload(form, document_payload)
    _required(
        payload,
        (
            "arklok_user_id",
            "customer_name",
            "customer_email",
            "customer_matriculation",
            "ticket",
            "equipament_type",
            "equipament_brand",
            "equipament_model",
            "equipament_serial",
            "return_date",
            "local",
        ),
        "Emprestimo",
    )
    response = _submit_form(session, form, payload, "/loan-term/criar", "post_emprestimo")
    if not (200 <= int(response["status"]) < 400):
        raise AssetDocumentSendError(f"POST Emprestimo retornou HTTP {response['status']}. Debug: {response['debug_html']}")
    verification = _verify_emprestimo(session, document_payload)
    if not verification.get("found"):
        raise AssetDocumentSendError(f"POST Emprestimo respondeu, mas nao confirmou na listagem. Debug: {response['debug_html']}")
    row = verification["matches"][0]
    return {"midiasimples_id": str(row.get("id") or ""), "response": response, "verification": verification}


def send_devolucao(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or {}
    devolucao = dados.get("devolucao") or {}
    registration = _text(colaborador.get("matricula"))
    if not registration:
        raise AssetDocumentSendError("Devolucao sem matricula do colaborador.")
    current, _previous_ids = _current_from_registration(session, registration)
    personal_email = _text(devolucao.get("email_pessoal") or colaborador.get("email"))
    if not personal_email:
        raise AssetDocumentSendError("Devolucao exige email pessoal do colaborador.")
    form = _get_form(session, "/termo-de-devolucao/criar", "/termo-de-devolucao", "devolucao_avulsa_criar")
    payload = _build_standalone_devolucao_payload(
        form,
        current,
        _text(devolucao.get("modo") or "total"),
        personal_email,
        _text(devolucao.get("observacao") or "DEVOLUCAO REGISTRADA PELO MD HUB."),
    )
    _required(payload, ("client_name", "registration", "client_email", "product_serial_number"), "Devolucao")
    response = _submit_form(session, form, payload, "/termo-de-devolucao/criar", "post_devolucao")
    if not (200 <= int(response["status"]) < 400):
        raise AssetDocumentSendError(f"POST Devolucao retornou HTTP {response['status']}. Debug: {response['debug_html']}")
    verification = _verify_devolucao(session, current, _upper(document_payload.get("numero_chamado")))
    if not verification.get("found"):
        raise AssetDocumentSendError(f"POST Devolucao respondeu, mas nao confirmou na listagem. Debug: {response['debug_html']}")
    row = verification["matches"][0]
    return {"midiasimples_id": str(row.get("id") or ""), "response": response, "verification": verification}


def send_concessao(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    current = _collaborator_from_payload(document_payload)
    previous_ids = {int(row.get("id") or 0) for row in _get_tim_users_by_registration(session, current["registration"]) if row.get("id")}
    new_machine = _new_machine_from_payload(document_payload)
    _required(current, ("name", "registration", "email"), "Colaborador")
    _required(new_machine, ("serial", "ticket", "brand", "profile"), "Concessao")
    return _create_concessao_for_current(
        session,
        current,
        previous_ids,
        new_machine,
        "concessao",
        new_machine.get("observation") or "CONCESSAO DE MAQUINA.",
    )


def _headset_from_payload(document_payload: dict[str, Any]) -> dict[str, str]:
    dados = document_payload.get("dados") or {}
    headset = dados.get("headset_novo") or {}
    model = _text(headset.get("modelo") or headset.get("model") or "POLY BW - 3220 USB-C")
    serial = _upper(headset.get("serial"))
    headset_model = _text(headset.get("headset_model"))
    if not headset_model:
        headset_model = " ".join(part for part in (model, f"S/N {serial}" if serial else "") if part)
    return {"model": model, "serial": serial, "headset_model": headset_model}


def _current_machine_for_headset_replacement(current: dict[str, Any], document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    tecnico = dados.get("tecnico") or {}
    term = current.get("term") or {}
    profile = _value(term, "customer_profile") or current.get("profile") or "PERFORMANCE"
    headset = _headset_from_payload(document_payload)
    return {
        "ticket": _upper(document_payload.get("numero_chamado") or dados.get("numero_chamado")),
        "serial": _upper(_value(term, "product_serial_number") or current.get("serial")),
        "brand": _text(_value(term, "product_brand") or "LENOVO"),
        "model": _text(_value(term, "product_model")),
        "profile": profile,
        "nf": _text(_value(term, "incoice_number") or _value(term, "invoice_number")),
        "patrimony": _text(_value(term, "product_number")),
        "hostname": _upper(_value(term, "hostname")),
        "scheduled_time": "17:00",
        "responsible_name": _text(tecnico.get("full_name") or tecnico.get("display_name")),
        "headset_model": headset["headset_model"],
        "headset_serial": headset["serial"],
        "observation": "SUBSTITUICAO DE HEADSET. MANTIDOS EQUIPAMENTO E DEMAIS PERIFERICOS DO TERMO ANTERIOR.",
    }


def send_substituicao_headset(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or {}
    registration = _text(colaborador.get("matricula"))
    if not registration:
        raise AssetDocumentSendError("Substituicao de headset sem matricula do colaborador.")
    headset = _headset_from_payload(document_payload)
    if not headset["serial"]:
        raise AssetDocumentSendError("Substituicao de headset sem serial do headset novo.")
    if not headset["headset_model"]:
        raise AssetDocumentSendError("Substituicao de headset sem modelo do headset novo.")
    current, previous_ids = _current_from_registration(session, registration)
    new_machine = _current_machine_for_headset_replacement(current, document_payload)
    _required(current, ("name", "registration", "email"), "Colaborador")
    _required(new_machine, ("serial", "ticket", "brand", "profile", "headset_model"), "Substituicao de headset")
    result = _create_concessao_for_current(
        session,
        current,
        previous_ids,
        new_machine,
        "substituicao_headset",
        new_machine["observation"],
    )
    result["headset_replaced"] = True
    result["headset_model"] = headset["headset_model"]
    return result


def send_substituicao(session: MidiaSimplesSession, document_payload: dict[str, Any]) -> dict[str, Any]:
    dados = document_payload.get("dados") or {}
    colaborador = dados.get("colaborador") or {}
    registration = _text(colaborador.get("matricula"))
    if not registration:
        raise AssetDocumentSendError("Substituicao sem matricula do colaborador.")
    try:
        current, previous_ids = _current_from_registration(session, registration)
        has_previous_term = True
    except AssetDocumentSendError as exc:
        text = str(exc)
        if "termo de concessao" not in text and "term_of_concession" not in text:
            raise
        current = _collaborator_from_payload(document_payload)
        previous_ids = {int(row.get("id") or 0) for row in _get_tim_users_by_registration(session, registration) if row.get("id")}
        has_previous_term = False
    new_machine = _new_machine_from_payload(document_payload)
    _required(current, ("name", "registration", "email"), "Colaborador")
    _required(new_machine, ("serial", "ticket", "brand", "profile"), "Substituicao")

    if not has_previous_term:
        result = _create_concessao_for_current(
            session,
            current,
            previous_ids,
            new_machine,
            "substituicao_sem_termo",
            new_machine.get("observation") or "CONCESSAO CRIADA SEM TERMO ANTERIOR LOCALIZADO NO MIDIASIMPLES.",
        )
        result["devolution_skipped"] = True
        result["devolution_skip_reason"] = f"Nao havia termo de concessao anterior com serial para a matricula {registration}."
        return result

    collaborator_form = _get_form(session, "/colaboradores-tim/criar", "/colaboradores-tim", "substituicao_colaborador_criar")
    collaborator_payload = _build_colaborador_payload(collaborator_form, current, new_machine)
    collaborator_response = _submit_form(session, collaborator_form, collaborator_payload, "/colaboradores-tim/criar", "post_substituicao_colaborador")
    if not (200 <= int(collaborator_response["status"]) < 400):
        raise AssetDocumentSendError(f"Falha ao criar colaborador: HTTP {collaborator_response['status']}. Debug: {collaborator_response['debug_html']}")

    new_id = _find_new_collaborator_id(session, registration, previous_ids, new_machine["profile"])
    if not new_id:
        raise AssetDocumentSendError(f"Colaborador criado, mas nao encontrei novo ID para matricula {registration}. Debug: {collaborator_response['debug_html']}")

    devolution_form = _get_form(session, f"/termos-de-devolucao/{new_id}/criar", "/colaboradores-tim", "substituicao_devolucao_criar")
    devolution_payload = _build_envelope_devolucao_payload(devolution_form, current)
    devolution_response = _submit_form(
        session,
        devolution_form,
        devolution_payload,
        f"/termos-de-devolucao/{new_id}/criar",
        "post_substituicao_devolucao",
    )
    if not (200 <= int(devolution_response["status"]) < 400):
        raise AssetDocumentSendError(f"Falha ao criar devolucao da substituicao: HTTP {devolution_response['status']}. Debug: {devolution_response['debug_html']}")

    concession_form = _get_form(session, f"/colaboradores-tim/{new_id}/termo-concessao/criar", "/colaboradores-tim", "substituicao_concessao_criar")
    concession_payload = _build_concessao_payload(concession_form, current, new_machine)
    concession_response = _submit_form(
        session,
        concession_form,
        concession_payload,
        f"/colaboradores-tim/{new_id}/termo-concessao/criar",
        "post_substituicao_concessao",
    )
    if not (200 <= int(concession_response["status"]) < 400):
        raise AssetDocumentSendError(f"Devolucao enviada, mas concessao falhou: HTTP {concession_response['status']}. Debug: {concession_response['debug_html']}")
    verification = _verify_concessao(session, current, new_machine, new_id)
    if not verification.get("found"):
        raise AssetDocumentSendError(f"POST Substituicao respondeu, mas concessao nova nao confirmou na listagem. Debug: {concession_response['debug_html']}")
    row = verification["matches"][0]
    return {
        "midiasimples_id": str(row.get("id") or new_id),
        "colaborador_id": new_id,
        "colaborador_response": collaborator_response,
        "devolution_response": devolution_response,
        "concession_response": concession_response,
        "verification": verification,
    }
