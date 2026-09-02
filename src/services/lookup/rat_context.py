from typing import Any

from src.services.midiasimples.session_store import get_recent_validated_session
from src.services.midiasimples.asset_senders import (
    _datatable_search,
    _get_tim_user_detail,
    _get_tim_users_by_registration,
    _select_current_concession,
    _status_is_voided,
)
from src.services.lookup.asset_lookup import AssetLookup
from src.services.lookup.person_lookup import PersonLookup
from src.services.inventory.normalizer import DataNormalizer


class RatContextService:
    """Monta o contexto operacional que futuramente sera consumido pela RAT."""

    def __init__(self):
        self.assets = AssetLookup()
        self.people = PersonLookup()

    def build(self, query: str) -> dict[str, Any]:
        asset = self.assets.find(query)
        person = self.people.find(query)
        initial_person = person.get("preferred") or {}
        person_identifier = initial_person.get("matricula_key") or initial_person.get("matricula")
        if person_identifier and not asset.get("found"):
            asset = self.assets.find(str(person_identifier))
        preferred_asset = asset.get("preferred") or {}
        asset_sources = asset.get("sources") or []
        preferred_person = person.get("preferred") or {}
        automatos = self._source_data(asset, "automatos_snapshot")
        midiasimples = self._midiasimples_context(
            preferred_person.get("matricula_key")
            or preferred_person.get("matricula")
            or automatos.get("top_user_key")
            or preferred_asset.get("matricula_atual")
            or preferred_asset.get("matricula")
            or query,
            preferred_serial=automatos.get("serial") or preferred_asset.get("serial"),
        )
        midiasimples_term = midiasimples.get("term") if midiasimples else {}
        midia = midiasimples or {}
        term = midiasimples_term or {}

        return {
            "query": query,
            "asset_found": asset["found"],
            "person_found": person["found"] or bool(midiasimples),
            "operational": {
                "matricula": self._first(
                    midia.get("matricula"),
                    preferred_asset.get("matricula"),
                    preferred_asset.get("matricula_atual"),
                    preferred_asset.get("top_user"),
                    preferred_person.get("matricula"),
                ),
                "nome": self._first(midia.get("nome"), preferred_asset.get("nome"), preferred_asset.get("usuario_atual"), preferred_person.get("nome")),
                "email": self._first(midia.get("email"), preferred_asset.get("email"), preferred_person.get("email")),
                "cargo": self._first(midia.get("cargo"), preferred_asset.get("cargo"), preferred_person.get("cargo")),
                "serial": self._first(preferred_asset.get("serial"), midia.get("serial"), term.get("product_serial_number")),
                "patrimonio": self._first(
                    preferred_asset.get("patrimonio"),
                    term.get("patrimony"),
                    term.get("patrimonio"),
                    term.get("product_number"),
                    term.get("asset_tag"),
                    self._first_source_value(asset_sources, "patrimonio"),
                ),
                "nota_fiscal": self._first(
                    preferred_asset.get("nota_fiscal"),
                    preferred_asset.get("nf"),
                    term.get("invoice_number"),
                    term.get("incoice_number"),
                    term.get("nota_fiscal"),
                    self._first_source_value(asset_sources, "nota_fiscal"),
                    self._first_source_value(asset_sources, "nf"),
                ),
                "hostname": preferred_asset.get("hostname")
                or preferred_asset.get("computer_name")
                or term.get("hostname")
                or term.get("host_name"),
                "marca": self._first(preferred_asset.get("marca"), preferred_asset.get("manufacturer"), term.get("product_brand")),
                "modelo": self._first(preferred_asset.get("modelo"), term.get("product_model"), preferred_asset.get("system_product_name")),
                "tipo": preferred_asset.get("tipo")
                or preferred_asset.get("categoria")
                or term.get("product_category")
                or preferred_asset.get("computer_type"),
                "status_atual": preferred_asset.get("status_atual"),
                "status_confianca": preferred_asset.get("status_confianca"),
                "fonte": self._join_sources(preferred_asset.get("_source"), "midiasimples" if midiasimples else None),
                "automatos": self._automatos_context(automatos),
                "midiasimples": midiasimples,
            },
            "asset": asset,
            "person": person,
            "alerts": self._alerts(asset, person, midiasimples),
        }

    def _source_data(self, asset: dict[str, Any], source_name: str) -> dict[str, Any]:
        for source in asset.get("sources", []):
            if source.get("source") == source_name:
                return source.get("data") or {}
        return {}

    def _automatos_context(self, automatos: dict[str, Any]) -> dict[str, Any] | None:
        if not automatos:
            return None
        return {
            "hostname": automatos.get("computer_name") or automatos.get("computer_name_key"),
            "usuario": automatos.get("top_user"),
            "usuario_key": automatos.get("top_user_key"),
            "coleta": automatos.get("collect_date"),
            "ultima_atualizacao": automatos.get("update_date"),
            "status": automatos.get("status"),
            "tipo": automatos.get("computer_type"),
            "fabricante": automatos.get("manufacturer"),
            "modelo_tecnico": automatos.get("system_product_name"),
            "sistema": automatos.get("operating_system"),
            "processador": automatos.get("processor"),
            "memoria": automatos.get("memory") or automatos.get("installed_mem"),
            "disco_total": automatos.get("disk_total"),
            "disco_usado": automatos.get("disk_used"),
            "ip": automatos.get("ip_address"),
            "machine_id": automatos.get("machine_id"),
            "escopo": automatos.get("regional_scope"),
        }

    def _midiasimples_context(self, identifier: Any, preferred_serial: Any = None) -> dict[str, Any] | None:
        matricula = DataNormalizer.matricula_key(identifier)
        if not matricula:
            return None
        stored = get_recent_validated_session("/colaboradores-tim")
        if not stored:
            return None
        try:
            rows = _get_tim_users_by_registration(stored.session, matricula)
        except Exception:
            rows = []

        if not rows:
            try:
                result = _datatable_search(stored.session, "/colaboradores-tim", matricula, limit=50)
                rows = result.get("rows") or []
            except Exception:
                rows = []

        candidates: list[dict[str, Any]] = []
        for row in rows:
            row_matricula = DataNormalizer.matricula_key(row.get("registration"))
            if row_matricula != matricula:
                continue
            term = row.get("term_of_concession") or {}
            if not isinstance(term, dict):
                term = {}
            if not term:
                term = {
                    "product_serial_number": row.get("serial") or row.get("product_serial_number"),
                    "product_brand": row.get("product_brand") or row.get("brand"),
                    "product_model": row.get("product_model") or row.get("model"),
                    "product_category": row.get("product_category") or row.get("category"),
                    "patrimony": row.get("patrimony") or row.get("patrimonio"),
                    "invoice_number": row.get("invoice_number") or row.get("nota_fiscal"),
                }
            candidates.append({**row, "term_of_concession": term})
        if not candidates:
            return None

        person_row = _select_current_concession(candidates) or self._latest_row(candidates)
        term_row = self._select_term_row(candidates, preferred_serial) or person_row

        try:
            if person_row and person_row.get("id"):
                detail = _get_tim_user_detail(stored.session, person_row["id"])
                if isinstance(detail, dict):
                    person_row = {**person_row, **detail}
        except Exception:
            pass

        row = person_row or term_row or {}
        term = (term_row or row).get("term_of_concession") or {}
        return {
            "id": row.get("id"),
            "matricula": DataNormalizer.matricula_key(row.get("registration")),
            "nome": row.get("name"),
            "email": row.get("email"),
            "telefone": row.get("cellphone"),
            "cargo": row.get("role") or row.get("profile"),
            "perfil": row.get("profile"),
            "regional": row.get("subsidiary"),
            "status_docusign": row.get("docusign_status"),
            "term_status_docusign": (term_row or {}).get("docusign_status"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "serial": term.get("product_serial_number") or row.get("serial"),
            "latest_serial": ((person_row or {}).get("term_of_concession") or {}).get("product_serial_number"),
            "serial_confirmado_por_automatos": bool(
                DataNormalizer.normalize_serial(preferred_serial)
                and DataNormalizer.normalize_serial(preferred_serial) == DataNormalizer.normalize_serial(term.get("product_serial_number"))
            ),
            "term": term,
        }

    def _latest_row(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        rows = [row for row in rows if row]
        rows.sort(
            key=lambda row: (str(row.get("updated_at") or ""), str(row.get("created_at") or ""), int(row.get("id") or 0)),
            reverse=True,
        )
        return rows[0] if rows else None

    def _select_term_row(self, rows: list[dict[str, Any]], preferred_serial: Any) -> dict[str, Any] | None:
        serial = DataNormalizer.normalize_serial(preferred_serial)
        if not serial:
            return None
        matches = []
        for row in rows:
            term = row.get("term_of_concession") or {}
            term_serial = DataNormalizer.normalize_serial(term.get("product_serial_number") or row.get("serial"))
            if term_serial == serial:
                matches.append(row)
        if not matches:
            return None
        valid = [row for row in matches if not _status_is_voided(row.get("docusign_status"))]
        return self._latest_row(valid or matches)

    def _alerts(self, asset: dict[str, Any], person: dict[str, Any], midiasimples: dict[str, Any] | None = None) -> list[str]:
        alerts = []
        preferred_asset = asset.get("preferred") or {}
        preferred_person = person.get("preferred") or {}
        automatos = self._source_data(asset, "automatos_snapshot")
        if preferred_asset.get("laudo_alerta"):
            alerts.append(f"Ativo possui laudo: {preferred_asset.get('laudo_tipo') or 'verificar'}")
        if preferred_person.get("status_rh") and str(preferred_person["status_rh"]).upper() not in {"ATIVO", "A"}:
            alerts.append(f"Pessoa com status RH {preferred_person['status_rh']}")
        if automatos:
            alerts.append(
                "Automatos encontrado: "
                f"{automatos.get('computer_name_key') or automatos.get('computer_name')} "
                f"em {automatos.get('collect_date') or 'data sem coleta'}."
            )
            asset_user_key = self._first_non_automatos_user_key(asset) or DataNormalizer.matricula_key(
                preferred_asset.get("matricula") or preferred_asset.get("matricula_atual")
            )
            if asset_user_key and automatos.get("top_user_key") and asset_user_key != automatos.get("top_user_key"):
                alerts.append(
                    "Automatos mostra usuario diferente da base local: "
                    f"{automatos.get('top_user')}."
                )
            if automatos.get("regional_scope") == "OUTRAS_REGIONAIS":
                alerts.append("Automatos fora do escopo RJ_CEO; manter pesquisavel, fora dos totais principais.")
        if midiasimples:
            alerts.append(
                "MidiaSimples termo relacionado: "
                f"{midiasimples.get('serial') or 'serial sem termo'} "
                f"({midiasimples.get('term_status_docusign') or midiasimples.get('status_docusign') or 'status sem docusign'})."
            )
            auto_serial = DataNormalizer.normalize_serial(automatos.get("serial")) if automatos else None
            midia_serial = DataNormalizer.normalize_serial(midiasimples.get("serial"))
            latest_serial = DataNormalizer.normalize_serial(midiasimples.get("latest_serial"))
            if auto_serial and latest_serial and auto_serial != latest_serial:
                alerts.append(
                    "Automatos e termo mais recente do MidiaSimples apontam seriais diferentes: "
                    f"Automatos {auto_serial} x termo recente {latest_serial}."
                )
            if midia_serial and _status_is_voided(midiasimples.get("term_status_docusign")):
                alerts.append(f"Termo MidiaSimples que confirma o serial {midia_serial} esta voided/cancelado.")
        if not asset.get("found"):
            alerts.append("Nenhum ativo encontrado para a consulta.")
        return alerts

    def _first_non_automatos_user_key(self, asset: dict[str, Any]) -> str | None:
        for source in asset.get("sources", []):
            if source.get("source") == "automatos_snapshot":
                continue
            data = source.get("data") or {}
            key = DataNormalizer.matricula_key(
                data.get("matricula") or data.get("matricula_atual") or data.get("usuario_atual")
            )
            if key:
                return key
        return None

    def _join_sources(self, *sources: Any) -> str:
        parts = []
        for source in sources:
            if not source:
                continue
            for item in str(source).split("+"):
                item = item.strip()
                if item and item not in parts:
                    parts.append(item)
        return "+".join(parts)

    def _first(self, *values: Any) -> Any:
        for value in values:
            if value not in (None, "", "-", "N/A", "NA", "---------", "-------"):
                return value
        return None

    def _first_source_value(self, sources: list[dict[str, Any]], field: str) -> Any:
        for source in sources:
            data = source.get("data") or {}
            value = data.get(field)
            if value not in (None, "", "-", "N/A", "NA", "---------", "-------"):
                return value
        return None
