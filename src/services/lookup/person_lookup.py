from typing import Any
import json

from src.core.database import connect
from src.core.db_session import SessionLocal
from src.models.core import Collaborator, WhatsAppContact
from src.services.inventory.normalizer import DataNormalizer


def _row_to_dict(row) -> dict[str, Any] | None:
    return dict(row) if row else None


class PersonLookup:
    def find(self, query: str) -> dict[str, Any]:
        query = (query or "").strip()
        matricula_key = DataNormalizer.matricula_key(query)
        normalized_name = query.upper()
        sources = []

        sources.extend(self._find_core_people(query, matricula_key))

        with connect() as conn:
            person = self._find_people_registry(conn, matricula_key, normalized_name)
            if person:
                sources.append({"source": "people_registry", "data": person})

            sap = self._find_report_sap_tim(conn, matricula_key, normalized_name)
            if sap:
                sources.append({"source": "report_sap_tim", "data": sap})

            legacy = self._find_legacy_people(conn, matricula_key, normalized_name)
            sources.extend(legacy)

            rat_history = self._find_rat_history_people(conn, matricula_key, normalized_name)
            sources.extend(rat_history)

            operation_logs = self._find_operation_log_people(conn, matricula_key, normalized_name)
            sources.extend(operation_logs)

        return {
            "found": bool(sources),
            "query": query,
            "normalized": {"matricula_key": matricula_key, "name": normalized_name},
            "preferred": self._merge_preferred(sources),
            "sources": sources,
        }

    def _find_core_people(self, query: str, matricula_key: str | None) -> list[dict[str, Any]]:
        db = SessionLocal()
        try:
            collaborator = self._choose_core_person(db.query(Collaborator).all(), query, matricula_key)
            contact_rows = self._contact_candidates(db, query, matricula_key)
            contact = self._choose_core_person(contact_rows, query, matricula_key)

            resolved_mat = DataNormalizer.matricula_key(
                getattr(collaborator, "matricula", None) or getattr(contact, "matricula", None)
            )
            if resolved_mat:
                if not collaborator or DataNormalizer.matricula_key(collaborator.matricula) != resolved_mat:
                    collaborator = db.query(Collaborator).filter(Collaborator.matricula == resolved_mat).first()
                if not contact or DataNormalizer.matricula_key(contact.matricula) != resolved_mat:
                    contact = db.query(WhatsAppContact).filter(WhatsAppContact.matricula == resolved_mat).order_by(
                        WhatsAppContact.updated_at.desc(), WhatsAppContact.id.desc()
                    ).first()

            results: list[dict[str, Any]] = []
            if collaborator:
                results.append({"source": "mdhub_core_colaboradores",
                                "data": self._core_person_data(collaborator, "mdhub_core_colaboradores")})
            if contact:
                source = contact.fonte or "whatsapp_contacts"
                results.append({"source": source, "data": self._core_person_data(contact, source)})
            return results
        finally:
            db.close()

    def _contact_candidates(self, db, query: str, matricula_key: str | None) -> list[WhatsAppContact]:
        text = (query or "").strip()
        digits = "".join(char for char in text if char.isdigit())
        query_db = db.query(WhatsAppContact)
        if matricula_key:
            return query_db.filter(WhatsAppContact.matricula == matricula_key).limit(20).all()
        if "@" in text:
            return query_db.filter(WhatsAppContact.email.ilike(text)).limit(20).all()
        if len(digits) == 4 and text.isdigit():
            return query_db.filter(WhatsAppContact.telefone_formatado.like(f"%{digits}")).limit(50).all()
        if len(digits) >= 8:
            return query_db.filter(WhatsAppContact.telefone_formatado.like(f"%{digits}")).limit(20).all()
        tokens = [token for token in text.split() if len(DataNormalizer.clean_key(token)) >= 2]
        if tokens:
            query_db = query_db.filter(WhatsAppContact.nome.ilike(f"%{tokens[0]}%"))
            return query_db.limit(100).all()
        return []

    def _core_person_data(self, person: Any, source: str) -> dict[str, Any]:
        updated_at = getattr(person, "updated_at", None)
        return {
            "matricula": getattr(person, "matricula", None),
            "matricula_key": DataNormalizer.matricula_key(getattr(person, "matricula", None)),
            "nome": getattr(person, "nome", None),
            "telefone": getattr(person, "telefone", None) or getattr(person, "telefone_formatado", None),
            "email": getattr(person, "email", None),
            "cargo": getattr(person, "cargo", None),
            "regional": getattr(person, "regional", None),
            "status_rh": getattr(person, "status", None),
            "source_name": source,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

    def _choose_core_person(self, rows: list[Collaborator], query: str,
                            matricula_key: str | None) -> Collaborator | None:
        query_text = (query or "").strip()
        query_key = DataNormalizer.clean_key(query_text)
        query_name_tokens = [DataNormalizer.clean_key(token) for token in query_text.split()]
        query_name_tokens = [token for token in query_name_tokens if len(token) >= 2]
        query_digits = "".join(char for char in query_text if char.isdigit())
        phone_suffix = query_digits if len(query_digits) == 4 and query_text.isdigit() else ""
        candidates: list[tuple[int, str, Collaborator]] = []
        for item in rows:
            if getattr(item, "fonte", None) == "teste_ia_ficticio":
                continue
            item_mat = DataNormalizer.matricula_key(getattr(item, "matricula", None))
            name_key = DataNormalizer.clean_key(getattr(item, "nome", None))
            email_key = DataNormalizer.clean_key(getattr(item, "email", None))
            phone_value = getattr(item, "telefone", None) or getattr(item, "telefone_formatado", None)
            phone_digits = "".join(char for char in str(phone_value or "") if char.isdigit())
            score = 0
            if matricula_key and item_mat == matricula_key:
                score = max(score, 120)
            if phone_suffix and phone_digits.endswith(f"98113{phone_suffix}"):
                score = max(score, 118)
            if phone_suffix and phone_digits.endswith(phone_suffix):
                score = max(score, 115)
            if len(query_digits) >= 8 and phone_digits and (
                phone_digits.endswith(query_digits) or query_digits.endswith(phone_digits)
            ):
                score = max(score, 110)
            if query_key and email_key and query_key == email_key:
                score = max(score, 105)
            if query_key and name_key == query_key:
                score = max(score, 100)
            elif len(query_key) >= 3 and query_key in name_key:
                score = max(score, 80)
            elif query_name_tokens and all(token in name_key for token in query_name_tokens):
                score = max(score, 75)
            if score:
                candidates.append((score, name_key, item))
        if not candidates:
            return None
        candidates.sort(key=lambda value: (-value[0], value[1], getattr(value[2], "id", 0) or 0))
        return candidates[0][2]

    def _find_people_registry(self, conn, matricula_key, normalized_name):
        clauses = []
        params = []
        if matricula_key:
            clauses.append("matricula_key = ?")
            params.append(matricula_key)
        if normalized_name:
            clauses.append("UPPER(nome) LIKE ?")
            params.append(f"%{normalized_name}%")
        if not clauses:
            return None
        try:
            row = conn.execute(
                f"SELECT * FROM people_registry WHERE {' OR '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        except Exception:
            return None
        return _row_to_dict(row)

    def _find_report_sap_tim(self, conn, matricula_key, normalized_name):
        clauses = []
        params = []
        if matricula_key:
            clauses.append("matricula_key = ?")
            params.append(matricula_key)
        if normalized_name:
            clauses.append("UPPER(u_nome_completo) LIKE ?")
            params.append(f"%{normalized_name}%")
        if not clauses:
            return None
        try:
            row = conn.execute(
                f"SELECT * FROM report_sap_tim WHERE {' OR '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
        except Exception:
            return None
        data = _row_to_dict(row)
        if not data:
            return None
        return {
            "matricula": data.get("u_matricula") or data.get("matricula_key"),
            "matricula_key": data.get("matricula_key"),
            "nome": data.get("u_nome_completo") or " ".join(
                value for value in (data.get("u_nome"), data.get("u_sobrenome")) if value
            ),
            "telefone": data.get("u_numcelular"),
            "email": data.get("u_emailtim"),
            "cargo": data.get("u_cargorh"),
            "regional": data.get("u_regional") or data.get("u_regional_terceiro"),
            "status_rh": data.get("u_descrstatus") or data.get("u_status"),
            "tipo_pessoa": data.get("tipo_pessoa"),
            "source_name": "report_sap_tim",
            "raw_payload": data,
        }

    def _find_legacy_people(self, conn, matricula_key, normalized_name):
        results = []
        for table in ("clientes", "bd_local_ceo_nakrj"):
            clauses = []
            params = []
            if matricula_key:
                clauses.append("matricula = ?")
                params.append(matricula_key)
            if normalized_name:
                clauses.append("UPPER(nome) LIKE ?")
                params.append(f"%{normalized_name}%")
            if not clauses:
                continue
            try:
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE {' OR '.join(clauses)} LIMIT 1",
                    params,
                ).fetchone()
            except Exception:
                row = None
            if row:
                results.append({"source": table, "data": dict(row)})
        return results

    def _find_rat_history_people(self, conn, matricula_key, normalized_name):
        clauses = []
        params = []
        if matricula_key:
            clauses.append("matricula = ?")
            params.append(matricula_key)
        if normalized_name:
            clauses.append("UPPER(colaborador) LIKE ?")
            params.append(f"%{normalized_name}%")
        if not clauses:
            return []
        try:
            rows = conn.execute(
                f"""
                SELECT matricula, colaborador, telefone, email, perfil, departamento, criado_em
                FROM historico_rat
                WHERE {' OR '.join(clauses)}
                ORDER BY criado_em DESC, rowid DESC
                LIMIT 3
                """,
                params,
            ).fetchall()
        except Exception:
            return []

        results = []
        for row in rows:
            data = dict(row)
            results.append(
                {
                    "source": "historico_rat",
                    "data": {
                        "matricula": data.get("matricula"),
                        "matricula_key": DataNormalizer.matricula_key(data.get("matricula")),
                        "nome": data.get("colaborador"),
                        "telefone": data.get("telefone"),
                        "email": data.get("email"),
                        "cargo": data.get("perfil") or data.get("departamento"),
                        "source_name": "historico_rat",
                        "updated_at": data.get("criado_em"),
                    },
                }
            )
        return results

    def _find_operation_log_people(self, conn, matricula_key, normalized_name):
        clauses = []
        params = []
        if matricula_key:
            clauses.append("(matricula = ? OR payload_json LIKE ?)")
            params.extend([matricula_key, f"%{matricula_key}%"])
        if normalized_name:
            clauses.append("(UPPER(nome) LIKE ? OR UPPER(payload_json) LIKE ?)")
            params.extend([f"%{normalized_name}%", f"%{normalized_name}%"])
        if not clauses:
            return []
        try:
            rows = conn.execute(
                f"""
                SELECT matricula, nome, email, payload_json, created_at
                FROM operation_logs
                WHERE {' OR '.join(clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT 3
                """,
                params,
            ).fetchall()
        except Exception:
            return []

        results = []
        for row in rows:
            raw = dict(row)
            payload = self._payload_dict(raw.get("payload_json"))
            matricula = raw.get("matricula") or payload.get("matricula")
            nome = raw.get("nome") or payload.get("nome")
            data = {
                "matricula": matricula,
                "matricula_key": DataNormalizer.matricula_key(matricula),
                "nome": nome,
                "telefone": payload.get("telefone"),
                "email": raw.get("email") or payload.get("email"),
                "cargo": payload.get("cargo") or payload.get("perfil"),
                "source_name": "operation_logs",
                "updated_at": raw.get("created_at"),
            }
            if data.get("matricula") or data.get("nome"):
                results.append({"source": "operation_logs", "data": data})
        return results

    def _payload_dict(self, payload_json: str | None) -> dict[str, Any]:
        if not payload_json:
            return {}
        try:
            payload = json.loads(payload_json)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _merge_preferred(self, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not sources:
            return None

        merged: dict[str, Any] = {}
        source_names = []
        for source in sources:
            source_name = source.get("source") or "fonte"
            data = source.get("data") or {}
            source_names.append(source_name)

            # Mantem o primeiro valor confiavel por campo, mas permite que
            # fontes complementares preencham lacunas da ficha do colaborador.
            for target, candidates in {
                "matricula": ("matricula", "registration"),
                "matricula_key": ("matricula_key", "matricula", "registration"),
                "nome": ("nome", "name", "colaborador"),
                "telefone": ("telefone", "phone", "celular"),
                "email": ("email",),
                "cargo": ("cargo", "perfil", "profile", "departamento"),
                "regional": ("regional", "subsidiary"),
                "status_rh": ("status_rh", "status"),
                "tipo_pessoa": ("tipo_pessoa",),
            }.items():
                if merged.get(target):
                    continue
                value = self._first(data.get(key) for key in candidates)
                if target == "matricula_key":
                    value = DataNormalizer.matricula_key(value)
                if target == "telefone":
                    value = DataNormalizer.normalize_phone(value)
                if value:
                    merged[target] = value

        if not merged.get("matricula_key") and merged.get("matricula"):
            merged["matricula_key"] = DataNormalizer.matricula_key(merged["matricula"])
        merged["_sources"] = source_names
        merged["_source"] = "+".join(dict.fromkeys(source_names))
        return merged

    def _first(self, values):
        for value in values:
            if value not in (None, "", "-", "---------", "-------"):
                return str(value).strip()
        return None
