from typing import Any

from src.services.inventory.normalizer import DataNormalizer
from src.services.inventory.document_model import resolve_document_model
from src.services.lookup.person_lookup import PersonLookup
from src.services.lookup.rat_context import RatContextService
from src.services.legacy.banco_clientes import buscar_equipamento_por_serial, normalizar_serial_busca
from src.core.db_session import SessionLocal
from src.models.core import Collaborator, Equipment


TEST_DATA_SOURCE = "teste_ia_ficticio"


def _first_value(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value).strip()
    return ""


class OperationalPrefillService:
    """Monta dados aplicaveis nas rotinas a partir do contexto operacional."""

    def __init__(self):
        self.context = RatContextService()
        self.people = PersonLookup()

    def build_for_rat(self, query: str) -> dict[str, Any]:
        test_result = self._test_data_prefill(query)
        if test_result:
            return test_result
        context = self.context.build(query)
        operational = context.get("operational") or {}
        automatos = operational.get("automatos") or {}
        person_query = self._is_person_query(query) or bool(context.get("person_found"))
        equipment = {} if person_query else self._equipment_from_query(query)
        query_serial = "" if person_query else normalizar_serial_busca(query)

        automatos_person = self._person_from_automatos(automatos)
        person = automatos_person or context.get("person", {}).get("preferred") or {}
        document_model = self._document_model(context, operational, automatos, equipment, person)

        matricula = _first_value(
            operational.get("matricula"),
            automatos.get("usuario"),
            person.get("matricula"),
        )
        matricula_key = DataNormalizer.matricula_key(matricula) or matricula

        dados = {
            "matricula": matricula_key,
            "nome": _first_value(operational.get("nome"), person.get("nome")),
            "telefone": _first_value(DataNormalizer.normalize_phone(person.get("telefone"))),
            "email": _first_value(operational.get("email"), person.get("email")),
            "cargo": _first_value(operational.get("cargo"), person.get("cargo")),
            "hostname": _first_value(operational.get("hostname"), automatos.get("hostname")),
            "marca": _first_value(operational.get("marca"), automatos.get("fabricante"), equipment.get("marca")),
            "modelo": document_model["modelo"],
            "modelo_tecnico": _first_value(automatos.get("modelo_tecnico"), operational.get("modelo")),
            "modelo_origem": document_model["origem"],
            "perfil_documental": document_model["perfil"],
            "modelo_opcoes": document_model["opcoes"],
            "serial": _first_value(operational.get("serial"), equipment.get("serial"), query_serial),
            "patrimonio": _first_value(operational.get("patrimonio"), equipment.get("patrimonio")),
            "nota_fiscal": _first_value(operational.get("nota_fiscal"), equipment.get("nota_fiscal")),
            "categoria": _first_value(operational.get("categoria"), equipment.get("categoria")),
            "_prefill_source": _first_value(
                self._join_sources(operational.get("fonte"), person.get("_source"), equipment.get("fonte"))
            ),
            "_prefill_query": query,
        }
        if self._serial_is_only_person_identifier(dados.get("serial"), dados.get("matricula")):
            dados["serial"] = ""

        missing_required = [
            label
            for label, value in (
                ("matricula", dados["matricula"]),
                ("nome", dados["nome"]),
                ("email", dados["email"]),
                ("cargo", dados["cargo"]),
            )
            if not value
        ]

        alerts = list(context.get("alerts") or [])
        alerts.extend(document_model.get("warnings") or [])

        return {
            "found": context.get("asset_found") or context.get("person_found"),
            "query": query,
            "dados": dados,
            "context": context,
            "automatos": automatos,
            "equipment": equipment,
            "alerts": alerts,
            "missing_required": missing_required,
            "can_apply": bool(dados["matricula"] or dados["serial"] or dados["hostname"]),
        }

    def _test_data_prefill(self, query: str) -> dict[str, Any] | None:
        """Expõe somente a seed fictícia aos wizards, sem misturá-la às fontes reais."""
        key = DataNormalizer.clean_key(query)
        if not key:
            return None
        db = SessionLocal()
        try:
            collaborators = db.query(Collaborator).filter(Collaborator.fonte == TEST_DATA_SOURCE).all()
            equipments = db.query(Equipment).filter(Equipment.fonte == TEST_DATA_SOURCE).all()
            collaborator = next(
                (
                    item for item in collaborators
                    if key in {
                        DataNormalizer.clean_key(item.matricula),
                        DataNormalizer.clean_key(item.email),
                        DataNormalizer.clean_key(item.nome),
                    }
                ),
                None,
            )
            equipment = next(
                (
                    item for item in equipments
                    if key in {
                        DataNormalizer.clean_key(item.serial),
                        DataNormalizer.clean_key(item.patrimonio),
                        DataNormalizer.clean_key(item.hostname),
                    }
                ),
                None,
            )
            if equipment and not collaborator:
                collaborator = db.get(Collaborator, equipment.colaborador_id) if equipment.colaborador_id else None
            if collaborator and not equipment:
                equipment = next((item for item in equipments if item.colaborador_id == collaborator.id), None)
            if not collaborator and not equipment:
                return None

            dados = {
                "matricula": collaborator.matricula if collaborator else "",
                "nome": collaborator.nome if collaborator else "",
                "telefone": "",
                "email": collaborator.email if collaborator else "",
                "cargo": collaborator.cargo if collaborator else "",
                "hostname": equipment.hostname if equipment else "",
                "marca": equipment.marca if equipment else "",
                "modelo": equipment.modelo if equipment else "",
                "modelo_tecnico": equipment.modelo_tecnico if equipment else "",
                "modelo_origem": TEST_DATA_SOURCE,
                "perfil_documental": "TESTE",
                "modelo_opcoes": [equipment.modelo] if equipment and equipment.modelo else [],
                "serial": equipment.serial if equipment else "",
                "patrimonio": equipment.patrimonio if equipment else "",
                "nota_fiscal": equipment.nota_fiscal if equipment else "",
                "categoria": equipment.categoria if equipment else "",
                "_prefill_source": TEST_DATA_SOURCE,
                "_prefill_query": query,
                "_test_data": True,
            }
            missing_required = [
                label
                for label, value in (
                    ("matricula", dados["matricula"]),
                    ("nome", dados["nome"]),
                    ("email", dados["email"]),
                    ("cargo", dados["cargo"]),
                )
                if not value
            ]
            return {
                "found": True,
                "query": query,
                "dados": dados,
                "context": {"test_data": True, "source": TEST_DATA_SOURCE},
                "automatos": {},
                "equipment": dados,
                "alerts": ["DADO FICTICIO DE TESTE - envio real e WhatsApp bloqueados."],
                "missing_required": missing_required,
                "can_apply": True,
            }
        finally:
            db.close()

    def _person_from_automatos(self, automatos: dict[str, Any]) -> dict[str, Any] | None:
        user = automatos.get("usuario") or automatos.get("usuario_key")
        if not user:
            return None
        result = self.people.find(str(user))
        return result.get("preferred") if result.get("found") else None

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

    def _document_model(
        self,
        context: dict[str, Any],
        operational: dict[str, Any],
        automatos: dict[str, Any],
        equipment: dict[str, Any],
        person: dict[str, Any],
    ) -> dict[str, Any]:
        documented_models: list[Any] = []
        profile_candidates: list[Any] = [
            equipment.get("perfil"),
            equipment.get("profile"),
            person.get("perfil"),
            person.get("profile"),
        ]

        asset = context.get("asset") or {}
        for source in asset.get("sources", []):
            data = source.get("data") or {}
            if source.get("source") != "automatos_snapshot":
                documented_models.extend(
                    [
                        data.get("modelo"),
                        data.get("model"),
                        data.get("product_model"),
                    ]
                )
                profile_candidates.extend(
                    [
                        data.get("perfil"),
                        data.get("profile"),
                        data.get("user_profile"),
                    ]
                )

        profile = _first_value(*profile_candidates)
        return resolve_document_model(
            profile=profile,
            documented_models=documented_models,
            automatos_model=automatos.get("modelo_tecnico") or operational.get("modelo"),
            fallback_model=equipment.get("modelo"),
        )

    def format_summary(self, prefill: dict[str, Any]) -> str:
        dados = prefill.get("dados") or {}
        automatos = prefill.get("automatos") or {}
        lines = [
            "Consulta operacional",
            "-" * 60,
            f"Fonte: {dados.get('_prefill_source') or '-'}",
            f"Matricula: {dados.get('matricula') or '-'}",
            f"Nome: {dados.get('nome') or '-'}",
            f"Email: {dados.get('email') or '-'}",
            f"Cargo: {dados.get('cargo') or '-'}",
            f"Serial: {dados.get('serial') or '-'}",
            f"Patrimonio: {dados.get('patrimonio') or '-'}",
            f"NF: {dados.get('nota_fiscal') or '-'}",
            f"Hostname: {dados.get('hostname') or '-'}",
            f"Marca/Modelo: {dados.get('marca') or '-'} / {dados.get('modelo') or '-'}",
        ]
        if dados.get("modelo_tecnico") and dados.get("modelo_tecnico") != dados.get("modelo"):
            lines.append(f"Modelo tecnico Automatos: {dados.get('modelo_tecnico')}")
        if automatos:
            lines.extend(
                [
                    "",
                    "Automatos",
                    f"Coleta: {automatos.get('coleta') or '-'}",
                    f"Usuario: {automatos.get('usuario') or '-'}",
                    f"Status: {automatos.get('status') or '-'}",
                    f"Memoria: {automatos.get('memoria') or '-'}",
                    f"Sistema: {automatos.get('sistema') or '-'}",
                    f"Escopo: {automatos.get('escopo') or '-'}",
                ]
            )
        if prefill.get("alerts"):
            lines.append("")
            lines.append("Alertas")
            lines.extend(f"- {alert}" for alert in prefill["alerts"])
        if prefill.get("missing_required"):
            lines.append("")
            lines.append("Campos que ainda precisam ser confirmados: " + ", ".join(prefill["missing_required"]))
        return "\n".join(lines)

    def _equipment_from_query(self, query: str) -> dict[str, Any]:
        if self._is_person_query(query):
            return {}
        serial = normalizar_serial_busca(query)
        if not serial:
            return {}
        try:
            return buscar_equipamento_por_serial(serial) or {}
        except Exception:
            return {}

    def _is_person_query(self, query: str) -> bool:
        text = str(query or "").strip()
        digits = "".join(char for char in text if char.isdigit())
        if "@" in text or (len(digits) == 4 and text.isdigit()):
            return True
        if any(char.isspace() for char in text) and sum(char.isalpha() for char in text) >= 3:
            return True
        key = DataNormalizer.clean_key(query)
        mat = DataNormalizer.matricula_key(key)
        if not mat or not mat.startswith("80"):
            return False
        if key == mat:
            return True
        return key.startswith(("F", "T")) and key[1:] == mat

    def _serial_is_only_person_identifier(self, serial: Any, matricula: Any) -> bool:
        serial_key = DataNormalizer.clean_key(serial)
        matricula_key = DataNormalizer.matricula_key(matricula)
        return bool(serial_key and matricula_key and serial_key == matricula_key)
