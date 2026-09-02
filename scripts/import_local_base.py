import json
import sqlite3
import sys
import argparse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parents[0]
sys.path.insert(0, str(ROOT))

from src.core.config import settings  # noqa: E402
from src.core.db_session import SessionLocal, create_database_schema  # noqa: E402
from src.models.core import AuditLog, Collaborator, Document, Equipment  # noqa: E402


def _resolve_local_db() -> Path:
    path = Path(settings.local_sqlite_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _rows(conn: sqlite3.Connection, table: str, limit: int = 0) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    try:
        sql = f"SELECT * FROM {table}"
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        return list(conn.execute(sql))
    except sqlite3.Error:
        return []


def _parse_json(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"raw": value}


def import_people(conn: sqlite3.Connection, db, limit: int = 0) -> int:
    count = 0
    for row in _rows(conn, "people_registry", limit=limit):
        matricula = (row["matricula_key"] or row["matricula"] or "").strip()
        if not matricula:
            continue
        collaborator = db.query(Collaborator).filter(Collaborator.matricula == matricula).first()
        if not collaborator:
            collaborator = Collaborator(matricula=matricula, nome=row["nome"] or matricula)
            db.add(collaborator)
        collaborator.nome = row["nome"] or collaborator.nome
        collaborator.email = row["email"] or collaborator.email
        collaborator.cargo = row["cargo"] or collaborator.cargo
        collaborator.regional = row["regional"] or collaborator.regional
        collaborator.status = row["status_rh"] or collaborator.status
        collaborator.fonte = row["source_name"] or "people_registry"
        count += 1
    return count


def import_assets(conn: sqlite3.Connection, db, limit: int = 0) -> int:
    count = 0
    for row in _rows(conn, "assets_current", limit=limit):
        serial = (row["serial"] or "").strip().upper()
        if not serial:
            continue

        collaborator = None
        matricula = (row["matricula_atual_key"] or row["matricula_atual"] or "").strip()
        if matricula:
            collaborator = db.query(Collaborator).filter(Collaborator.matricula == matricula).first()

        equipment = db.query(Equipment).filter(Equipment.serial == serial).first()
        if not equipment:
            equipment = Equipment(serial=serial)
            db.add(equipment)
        equipment.colaborador_id = collaborator.id if collaborator else equipment.colaborador_id
        equipment.patrimonio = row["patrimonio"] or equipment.patrimonio
        equipment.hostname = row["hostname"] or equipment.hostname
        equipment.categoria = row["tipo"] or equipment.categoria
        equipment.marca = row["marca"] or equipment.marca
        equipment.modelo = row["modelo"] or equipment.modelo
        equipment.nota_fiscal = row["nf"] or equipment.nota_fiscal
        equipment.status = row["status_atual"] or equipment.status
        equipment.fonte = row["status_fonte"] or "assets_current"
        equipment.payload = {
            "asset_class": row["asset_class"],
            "escopo_operacional": row["escopo_operacional"],
            "status_confianca": row["status_confianca"],
            "status_motivo": row["status_motivo"],
            "laudo_alerta": row["laudo_alerta"],
            "laudo_tipo": row["laudo_tipo"],
            "laudo_data": row["laudo_data"],
        }
        count += 1
    return count


def import_rat_history(conn: sqlite3.Connection, db, limit: int = 0) -> int:
    count = 0
    for row in _rows(conn, "historico_rat", limit=limit):
        source_id = row["source_id"]
        exists = db.query(Document).filter(Document.midiasimples_id == source_id, Document.tipo == "rat").first()
        if exists:
            continue
        collaborator = None
        matricula = (row["matricula"] or "").strip()
        if matricula:
            collaborator = db.query(Collaborator).filter(Collaborator.matricula == matricula).first()
        db.add(
            Document(
                tipo="rat",
                colaborador_id=collaborator.id if collaborator else None,
                numero_chamado=row["ticket"],
                midiasimples_id=source_id,
                status=row["status_docusign"] or "historico",
                payload={key: row[key] for key in row.keys()},
                sync_pendente=False,
            )
        )
        count += 1
    return count


def import_operation_logs(conn: sqlite3.Connection, db, limit: int = 0) -> int:
    count = 0
    for row in _rows(conn, "operation_logs", limit=limit):
        payload = _parse_json(row["payload_json"]) or {}
        db.add(
            AuditLog(
                acao=row["event_type"],
                modulo=row["module"],
                resultado=row["status"],
                payload={
                    "tipo": row["tipo"],
                    "nome": row["nome"],
                    "matricula": row["matricula"],
                    "email": row["email"],
                    "numero_chamado": row["numero_chamado"],
                    "serial": row["serial"],
                    "patrimonio": row["patrimonio"],
                    "payload": payload,
                },
            )
        )
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa a base SQLite local para o banco central.")
    parser.add_argument("--limit", type=int, default=0, help="Limita registros por tabela para validacao.")
    args = parser.parse_args()

    local_db = _resolve_local_db()
    if not local_db.exists():
        print(f"Banco local nao encontrado: {local_db}")
        return 2

    create_database_schema()
    conn = sqlite3.connect(local_db)
    conn.row_factory = sqlite3.Row
    with SessionLocal() as db:
        people = import_people(conn, db, limit=args.limit)
        db.commit()
        assets = import_assets(conn, db, limit=args.limit)
        db.commit()
        rats = import_rat_history(conn, db, limit=args.limit)
        db.commit()
        logs = import_operation_logs(conn, db, limit=args.limit)
        db.commit()

    print("Importacao local concluida.")
    print(f"Colaboradores processados: {people}")
    print(f"Equipamentos processados: {assets}")
    print(f"RATs historicas importadas: {rats}")
    print(f"Logs importados: {logs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
