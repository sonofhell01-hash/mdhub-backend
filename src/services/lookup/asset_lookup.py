import json
from datetime import datetime
from typing import Any

from sqlalchemy import or_

from src.core.database import connect
from src.core.db_session import SessionLocal
from src.models.core import Equipment
from src.services.inventory.filter_catalog import classify_asset
from src.services.inventory.normalizer import DataNormalizer


def _row_to_dict(row) -> dict[str, Any] | None:
    return dict(row) if row else None


def _compact(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    return {key: value for key, value in data.items() if value not in (None, "")}


class AssetLookup:
    """Consulta operacional de ativo.

    Primeiro tenta o modelo novo do HUB local (`assets_current`).
    Enquanto ele nao estiver populado, usa as bases legadas do RATScript.
    """

    def find(self, query: str) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"found": False, "query": query, "sources": []}

        serial = DataNormalizer.normalize_serial(query)
        patrimonio = DataNormalizer.normalize_patrimonio(query)
        hostname = DataNormalizer.normalize_hostname(query)
        matricula_key = DataNormalizer.matricula_key(query)
        person_query = self._is_person_query(query, matricula_key)
        if person_query:
            # Matriculas TIM 80xxxxx nao devem ser tratadas como serial/patrimonio.
            # Caso contrario, a consulta operacional acaba preenchendo serial=matricula.
            serial = None
            patrimonio = None

        sources: list[dict[str, Any]] = []
        with connect() as conn:
            current = self._find_current(conn, serial, patrimonio, hostname, matricula_key)
            if current:
                sources.append({"source": "assets_current", "data": current})

            automatos = self._find_automatos(conn, serial, patrimonio, hostname, matricula_key)
            if automatos:
                sources.append({"source": "automatos_snapshot", "data": automatos})

            legacy = self._find_legacy_assets(conn, serial, patrimonio, hostname, matricula_key)
            sources.extend({"source": item["source"], "data": item["data"]} for item in legacy)

            core_equipment = self._find_core_equipment(serial, patrimonio, hostname, matricula_key)
            if core_equipment:
                sources.append({"source": "mdhub_core_equipamentos", "data": core_equipment})

            source_serial = self._first_source_value(sources, "serial")
            derived_serial = source_serial or serial
            derived_patrimonio = patrimonio or self._first_source_value(sources, "patrimonio")
            nf = self._find_nf(conn, derived_serial, derived_patrimonio)
            if nf:
                sources.append({"source": "notas_fiscais_equipamentos", "data": nf})
                derived_serial = derived_serial or nf.get("serial")
                derived_patrimonio = derived_patrimonio or nf.get("patrimonio")

            # Patrimonio/NF podem revelar o serial; com ele buscamos o estado operacional atual.
            if derived_serial and (derived_serial != serial or source_serial):
                current_by_serial = self._find_current(conn, derived_serial, None, None, None)
                if current_by_serial and not self._has_source(sources, "assets_current", current_by_serial):
                    sources.append({"source": "assets_current", "data": current_by_serial})

                automatos_by_serial = self._find_automatos(conn, derived_serial, None, None, None)
                if automatos_by_serial and not self._has_source(sources, "automatos_snapshot", automatos_by_serial):
                    sources.append({"source": "automatos_snapshot", "data": automatos_by_serial})

            history = self._find_rat_history(conn, serial, patrimonio, hostname, matricula_key)

        preferred = self._choose_preferred(sources, prefer_principal=person_query)
        return {
            "found": bool(sources),
            "query": query,
            "normalized": {
                "serial": serial,
                "patrimonio": patrimonio,
                "hostname": hostname,
                "matricula_key": matricula_key,
            },
            "preferred": preferred,
            "sources": sources,
            "historico_rat": history,
        }

    def _find_current(self, conn, serial, patrimonio, hostname, matricula_key):
        clauses = []
        params = []
        if serial:
            clauses.append("serial = ?")
            params.append(serial)
        if patrimonio:
            clauses.append("patrimonio = ?")
            params.append(patrimonio)
        if hostname:
            clauses.append("hostname = ?")
            params.append(hostname)
        if matricula_key:
            clauses.append("matricula_atual_key = ?")
            params.append(matricula_key)
        if not clauses:
            return None
        try:
            row = conn.execute(
                f"SELECT * FROM assets_current WHERE {' OR '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        except Exception:
            return None
        return _compact(_row_to_dict(row))

    def _find_automatos(self, conn, serial, patrimonio, hostname, matricula_key):
        clauses = []
        params = []
        if serial:
            clauses.append("serial = ?")
            params.append(serial)
        if patrimonio:
            clauses.append("patrimonio = ?")
            params.append(patrimonio)
        if hostname:
            clauses.append("computer_name_key = ?")
            params.append(hostname)
        if matricula_key:
            clauses.append("top_user_key = ?")
            params.append(matricula_key)
        if not clauses:
            return None
        try:
            row = conn.execute(
                f"""
                SELECT * FROM automatos_snapshot
                WHERE {' OR '.join(clauses)}
                ORDER BY collect_date DESC, update_date DESC, id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        except Exception:
            return None
        return _compact(_row_to_dict(row))

    def _find_legacy_assets(self, conn, serial, patrimonio, hostname, matricula_key):
        results = []
        legacy_tables = [
            ("clientes", "matricula", "serial", None, "hostname"),
            ("bd_local_ceo_nakrj", "matricula", "serial", "patrimonio", "hostname"),
        ]
        for table, mat_col, serial_col, pat_col, host_col in legacy_tables:
            clauses = []
            params = []
            if serial and serial_col:
                clauses.append(f"{serial_col} = ?")
                params.append(serial)
            if patrimonio and pat_col:
                clauses.append(f"{pat_col} = ?")
                params.append(patrimonio)
            if hostname and host_col:
                clauses.append(f"{host_col} = ?")
                params.append(hostname)
            if matricula_key and mat_col:
                clauses.append(f"{mat_col} = ?")
                params.append(matricula_key)
            if not clauses:
                continue
            try:
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE {' OR '.join(clauses)} LIMIT 1",
                    params,
                ).fetchone()
            except Exception:
                row = None
            data = _compact(_row_to_dict(row))
            if data:
                results.append({"source": table, "data": data})
        return results

    def _find_nf(self, conn, serial, patrimonio):
        clauses = []
        params = []
        if serial:
            clauses.append("serial = ?")
            params.append(serial)
        if patrimonio:
            clauses.append("patrimonio = ?")
            params.append(patrimonio)
        if not clauses:
            return None
        try:
            row = conn.execute(
                f"SELECT * FROM notas_fiscais_equipamentos WHERE {' OR '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        except Exception:
            return None
        return _compact(_row_to_dict(row))

    def _find_core_equipment(self, serial, patrimonio, hostname, matricula_key):
        if not any((serial, patrimonio, hostname, matricula_key)):
            return None
        db = SessionLocal()
        try:
            query = db.query(Equipment)
            filters = []
            if serial:
                filters.append(Equipment.serial == serial)
            if patrimonio:
                filters.append(Equipment.patrimonio == patrimonio)
            if hostname:
                filters.append(Equipment.hostname == hostname)
            if matricula_key:
                filters.append(Equipment.payload["matricula"].as_string() == matricula_key)
            if not filters:
                return None
            row = query.filter(or_(*filters)).first()
            if not row:
                return None
            data = {
                "id": row.id,
                "colaborador_id": row.colaborador_id,
                "serial": row.serial,
                "patrimonio": row.patrimonio,
                "hostname": row.hostname,
                "categoria": row.categoria,
                "tipo": row.categoria,
                "marca": row.marca,
                "modelo": row.modelo,
                "modelo_tecnico": row.modelo_tecnico,
                "nota_fiscal": row.nota_fiscal,
                "status_atual": row.status,
                "fonte": row.fonte,
            }
            return _compact(data)
        finally:
            db.close()

    def _find_rat_history(self, conn, serial, patrimonio, hostname, matricula_key, limit=5):
        history = []
        seen = set()

        clauses = []
        params = []
        if serial:
            clauses.append("(serial = ? OR serial_anterior = ?)")
            params.extend([serial, serial])
        if patrimonio:
            clauses.append("(patrimonio = ? OR patrimonio_anterior = ?)")
            params.extend([patrimonio, patrimonio])
        if hostname:
            clauses.append("(hostname = ? OR hostname_anterior = ?)")
            params.extend([hostname, hostname])
        if matricula_key:
            clauses.append("matricula = ?")
            params.append(matricula_key)
        if not clauses:
            legacy_rows = []
        else:
            try:
                legacy_rows = conn.execute(
                    f"""
                    SELECT * FROM historico_rat
                    WHERE {' OR '.join(clauses)}
                    ORDER BY criado_em DESC
                    LIMIT ?
                    """,
                    [*params, limit],
                ).fetchall()
            except Exception:
                legacy_rows = []

        for row in legacy_rows:
            data = _compact(_row_to_dict(row))
            if not data:
                continue
            data["_history_source"] = "historico_rat"
            key = self._history_key(data)
            if key in seen:
                continue
            seen.add(key)
            history.append(data)

        for data in self._find_operation_log_rat_history(conn, serial, patrimonio, hostname, matricula_key, limit=limit):
            key = self._history_key(data)
            if key in seen:
                continue
            seen.add(key)
            history.append(data)

        history.sort(key=self._history_sort_key, reverse=True)
        return history[:limit]

    def _find_operation_log_rat_history(self, conn, serial, patrimonio, hostname, matricula_key, limit=5):
        search_clauses = []
        params = []
        if serial:
            search_clauses.append("(serial = ? OR payload_json LIKE ?)")
            params.extend([serial, f"%{serial}%"])
        if patrimonio:
            search_clauses.append("(patrimonio = ? OR payload_json LIKE ?)")
            params.extend([patrimonio, f"%{patrimonio}%"])
        if hostname:
            search_clauses.append("payload_json LIKE ?")
            params.append(f"%{hostname}%")
        if matricula_key:
            search_clauses.append("(matricula = ? OR payload_json LIKE ?)")
            params.extend([matricula_key, f"%{matricula_key}%"])
        if not search_clauses:
            return []

        try:
            rows = conn.execute(
                f"""
                SELECT *
                FROM operation_logs
                WHERE (event_type LIKE 'RAT%' OR tipo LIKE 'RAT%')
                  AND ({' OR '.join(search_clauses)})
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        except Exception:
            return []

        history = []
        for row in rows:
            raw = _compact(_row_to_dict(row))
            if not raw:
                continue
            payload = self._payload_dict(raw.get("payload_json"))
            data = {
                "source_id": f"operation_log:{raw.get('id')}",
                "matricula": raw.get("matricula") or payload.get("matricula"),
                "tecnico": payload.get("analista") or raw.get("usuario") or raw.get("username"),
                "ticket": raw.get("numero_chamado") or payload.get("chamado") or payload.get("numero_chamado"),
                "data_rat": raw.get("created_at"),
                "colaborador": raw.get("nome") or payload.get("nome"),
                "telefone": payload.get("telefone"),
                "email": raw.get("email") or payload.get("email"),
                "perfil": payload.get("cargo") or payload.get("perfil"),
                "serial": raw.get("serial") or payload.get("serial"),
                "patrimonio": raw.get("patrimonio") or payload.get("patrimonio"),
                "hostname": payload.get("hostname"),
                "descricao_problema": payload.get("problema") or payload.get("outro") or raw.get("message"),
                "descricao_fechamento": payload.get("fechamento"),
                "criado_em": raw.get("created_at"),
                "_history_source": "operation_logs",
                "_event_type": raw.get("event_type"),
                "_sort_date": raw.get("created_at"),
            }
            compacted = _compact(data)
            if compacted:
                history.append(compacted)
        return history

    def _payload_dict(self, payload_json: str | None) -> dict[str, Any]:
        if not payload_json:
            return {}
        try:
            payload = json.loads(payload_json)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _history_key(self, data: dict[str, Any]) -> tuple:
        return (
            data.get("ticket"),
            data.get("matricula"),
            data.get("serial"),
            data.get("criado_em") or data.get("data_rat"),
            data.get("_history_source"),
        )

    def _history_sort_key(self, data: dict[str, Any]) -> str:
        value = data.get("_sort_date") or data.get("criado_em") or data.get("data_rat") or ""
        text = str(value).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
            try:
                return datetime.strptime(text[:19], fmt).isoformat()
            except Exception:
                continue
        return text

    def _choose_preferred(self, sources: list[dict[str, Any]], prefer_principal: bool = False) -> dict[str, Any] | None:
        if not sources:
            return None
        priority = {
            "assets_current": 0,
            "automatos_snapshot": 1,
            "mdhub_core_equipamentos": 2,
            "bd_local_ceo_nakrj": 3,
            "clientes": 4,
            "notas_fiscais_equipamentos": 5,
        }

        def score(item):
            base = priority.get(item["source"], 99)
            if prefer_principal and self._is_peripheral_source(item):
                base += 20
            return base

        ordered = sorted(sources, key=score)
        preferred = dict(ordered[0]["data"])
        preferred["_source"] = ordered[0]["source"]
        return preferred

    def _is_person_query(self, query: str, matricula_key: str | None) -> bool:
        text = str(query or "").strip()
        digits = "".join(char for char in text if char.isdigit())
        if "@" in text or (len(digits) == 4 and text.isdigit()):
            return True
        if any(char.isspace() for char in text) and sum(char.isalpha() for char in text) >= 3:
            return True
        if not matricula_key:
            return False
        key = DataNormalizer.clean_key(query)
        if key.startswith(("F", "T")) and key[1:] == matricula_key:
            return matricula_key.startswith("80")
        return key == matricula_key and matricula_key.startswith("80")

    def _is_peripheral_source(self, item: dict[str, Any]) -> bool:
        data = item.get("data") or {}
        asset_class = str(data.get("asset_class") or "").upper()
        if asset_class == "PERIFERICO":
            return True
        tipo = data.get("tipo") or data.get("categoria") or data.get("computer_type")
        marca = data.get("marca") or data.get("manufacturer")
        modelo = data.get("modelo") or data.get("system_product_name")
        return classify_asset(tipo, data.get("serial"), marca, modelo) == "PERIFERICO"

    def as_json(self, query: str) -> str:
        return json.dumps(self.find(query), ensure_ascii=False, indent=2)

    def _first_source_value(self, sources: list[dict[str, Any]], field: str) -> str | None:
        for source in sources:
            data = source.get("data") or {}
            value = data.get(field)
            if value not in (None, "", "-", "N/A", "NA"):
                return str(value).strip()
        return None

    def _has_source(self, sources: list[dict[str, Any]], source_name: str, candidate: dict[str, Any]) -> bool:
        candidate_id = candidate.get("id")
        candidate_serial = DataNormalizer.normalize_serial(candidate.get("serial"))
        for source in sources:
            if source.get("source") != source_name:
                continue
            data = source.get("data") or {}
            if candidate_id is not None and data.get("id") == candidate_id:
                return True
            if candidate_serial and DataNormalizer.normalize_serial(data.get("serial")) == candidate_serial:
                return True
        return False
