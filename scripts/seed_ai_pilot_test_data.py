from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from src.core.config import settings
from src.core.db_session import SessionLocal
from src.models.core import Collaborator, Document, Equipment, User


MARKER = "seed_teste_ia_20260716"
SOURCE = "teste_ia_ficticio"
TICKET_MARKER = "-TESTE-IA-"


def database_path() -> Path:
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        raise RuntimeError("Este utilitario de seguranca aceita somente SQLite.")
    path = Path(settings.database_url[len(prefix):])
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def backup_database() -> Path:
    source_path = database_path()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "MDHUB" / "backups"
    target_dir = root / f"TEST_DATA_PRE_{datetime.now():%Y%m%d_%H%M%S}"
    target_dir.mkdir(parents=True, exist_ok=False)
    target_path = target_dir / source_path.name
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        result = target.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup SQLite invalido: {result}")
    finally:
        target.close()
        source.close()
    return target_path


USERS = [
    ("TECNICO FICTICIO ALPHA", "tecnico.teste.ia01@example.invalid"),
    ("TECNICO FICTICIO BETA", "tecnico.teste.ia02@example.invalid"),
    ("TECNICO FICTICIO GAMMA", "tecnico.teste.ia03@example.invalid"),
]

COLLABORATORS = [
    ("TESTEIA0001", "ANA COLABORADORA TESTE IA", "ana.teste.ia@example.invalid", "ANALISTA TESTE"),
    ("TESTEIA0002", "BRUNO COLABORADOR TESTE IA", "bruno.teste.ia@example.invalid", "COORDENADOR TESTE"),
    ("TESTEIA0003", "CARLA COLABORADORA TESTE IA", "carla.teste.ia@example.invalid", "ESPECIALISTA TESTE"),
    ("TESTEIA0004", "DIEGO COLABORADOR TESTE IA", "diego.teste.ia@example.invalid", "GERENTE TESTE"),
]

EQUIPMENTS = [
    ("TESTE-IA-NB-0001", "PAT-TESTE-0001", "TESTEIA-WS-0001", "LENOVO", "T14", "NOTEBOOK"),
    ("TESTE-IA-NB-0002", "PAT-TESTE-0002", "TESTEIA-WS-0002", "DELL", "LATITUDE 5450", "NOTEBOOK"),
    ("TESTE-IA-NB-0003", "PAT-TESTE-0003", "TESTEIA-WS-0003", "LENOVO", "T14 GEN4", "NOTEBOOK"),
    ("TESTE-IA-DT-0004", "PAT-TESTE-0004", "TESTEIA-WS-0004", "DELL", "OPTIPLEX TESTE", "DESKTOP"),
]

CALLS = [
    ("rat", "INC-TESTE-IA-0001", 0, 0, "Lentidao intermitente durante a inicializacao."),
    ("laudo", "INC-TESTE-IA-0002", 1, 1, "Equipamento apresenta falha de video durante os testes."),
    ("substituicao", "REQ-TESTE-IA-0003", 2, 2, "Substituicao ficticia para validar o roteiro da IA."),
    ("concessao", "REQ-TESTE-IA-0004", 3, 3, "Concessao ficticia de equipamento para teste."),
    ("devolucao", "REQ-TESTE-IA-0005", 0, 0, "Devolucao total ficticia para teste do fluxo."),
    ("emprestimo", "REQ-TESTE-IA-0006", 1, 1, "Emprestimo ficticio com devolucao prevista."),
    ("rollout", "REQ-TESTE-IA-0007", 2, 2, "Rollout ficticio para novo colaborador de teste."),
    ("fechamento", "INC-TESTE-IA-0008", 3, 3, "Fechamento ficticio baseado em RAT de teste."),
]


def seed() -> dict[str, int]:
    db = SessionLocal()
    created = {"usuarios": 0, "colaboradores": 0, "equipamentos": 0, "chamados": 0}
    try:
        users: list[User] = []
        for name, email in USERS:
            item = db.query(User).filter(User.email == email).first()
            if not item:
                item = User(nome=name, apelido="TESTE IA", email=email, perfil=SOURCE, ativo=False)
                db.add(item)
                db.flush()
                created["usuarios"] += 1
            users.append(item)

        collaborators: list[Collaborator] = []
        for registration, name, email, role in COLLABORATORS:
            item = db.query(Collaborator).filter(Collaborator.matricula == registration).first()
            if not item:
                item = Collaborator(
                    matricula=registration,
                    nome=name,
                    email=email,
                    telefone=None,
                    cargo=role,
                    regional="RJ-TESTE",
                    status="teste",
                    fonte=SOURCE,
                )
                db.add(item)
                db.flush()
                created["colaboradores"] += 1
            collaborators.append(item)

        equipments: list[Equipment] = []
        for index, (serial, patrimony, hostname, brand, model, category) in enumerate(EQUIPMENTS):
            item = db.query(Equipment).filter(Equipment.serial == serial).first()
            if not item:
                item = Equipment(
                    colaborador_id=collaborators[index].id,
                    serial=serial,
                    patrimonio=patrimony,
                    hostname=hostname,
                    categoria=category,
                    marca=brand,
                    modelo=model,
                    modelo_tecnico=f"MODELO-TECNICO-TESTE-{index + 1}",
                    nota_fiscal=f"NF-TESTE-{index + 1:04d}",
                    status="teste",
                    fonte=SOURCE,
                    payload={"_test_data": True, "_seed_marker": MARKER},
                )
                db.add(item)
                db.flush()
                created["equipamentos"] += 1
            equipments.append(item)

        for doc_type, ticket, collaborator_index, user_index, observation in CALLS:
            item = db.query(Document).filter(Document.numero_chamado == ticket).first()
            if item:
                continue
            collaborator = collaborators[collaborator_index]
            equipment = equipments[collaborator_index]
            user = users[user_index % len(users)]
            collaborator_data = {
                "matricula": collaborator.matricula,
                "nome": collaborator.nome,
                "email": collaborator.email,
                "telefone": None,
                "cargo": collaborator.cargo,
                "regional": collaborator.regional,
            }
            equipment_data = {
                "serial": equipment.serial,
                "patrimonio": equipment.patrimonio,
                "hostname": equipment.hostname,
                "marca": equipment.marca,
                "modelo": equipment.modelo,
                "categoria": equipment.categoria,
                "nota_fiscal": equipment.nota_fiscal,
            }
            details = {
                "_test_data": True,
                "_seed_marker": MARKER,
                "origem": SOURCE,
                "whatsapp_disabled": True,
                "colaborador": collaborator_data,
                "equipamento_atual": equipment_data,
                "tecnico": {
                    "display_name": user.nome,
                    "email": user.email,
                    "test_user": True,
                },
                "observacao": observation,
            }
            if doc_type == "rat":
                details["rat"] = {
                    "atendimento": {"other": "CENARIO FICTICIO"},
                    "textos": {
                        "problem_text": observation,
                        "close_text": "Testes ficticios realizados. Resultado ainda nao informado.",
                        "observations": "DADO FICTICIO - NAO ENVIAR.",
                    },
                }
            elif doc_type == "laudo":
                details["laudo"] = {
                    "uso_inadequado": False,
                    "textos": {
                        "acoes_executadas": "Foram realizados testes ficticios de inicializacao e video.",
                        "defeito_detectado": observation,
                        "descricao_analise": "Analise ficticia criada somente para validar a revisao por IA.",
                        "solucao": "Solucao nao definida no cenario ficticio.",
                    },
                }
            elif doc_type == "substituicao":
                details["equipamento_novo"] = {
                    "serial": "TESTE-IA-NOVO-0003",
                    "patrimonio": "PAT-TESTE-NOVO-0003",
                    "hostname": "TESTEIA-NOVO-0003",
                    "marca": "LENOVO",
                    "modelo": "T14 GEN6",
                    "categoria": "NOTEBOOK",
                }
            elif doc_type == "fechamento":
                details["fechamento"] = {
                    "resultado": "TESTE",
                    "texto": "Script ficticio. Nenhuma acao real deve ser executada.",
                }
            payload = {
                "_test_data": True,
                "_seed_marker": MARKER,
                "colaborador": collaborator_data,
                "dados": details,
            }
            db.add(Document(
                tipo=doc_type,
                colaborador_id=collaborator.id,
                usuario_id=user.id,
                numero_chamado=ticket,
                midiasimples_id=None,
                status="teste",
                payload=payload,
                response_payload={"source": SOURCE, "warning": "DADO FICTICIO - NAO ENVIAR"},
                sync_pendente=False,
                sync_tentativas=0,
            ))
            created["chamados"] += 1

        db.commit()
        return created
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def remove() -> dict[str, int]:
    db = SessionLocal()
    removed = {"chamados": 0, "equipamentos": 0, "colaboradores": 0, "usuarios": 0}
    try:
        documents = db.query(Document).filter(Document.numero_chamado.contains(TICKET_MARKER)).all()
        for item in documents:
            if (item.payload or {}).get("_seed_marker") == MARKER:
                db.delete(item)
                removed["chamados"] += 1
        db.flush()
        for item in db.query(Equipment).filter(Equipment.fonte == SOURCE).all():
            db.delete(item)
            removed["equipamentos"] += 1
        db.flush()
        for item in db.query(Collaborator).filter(Collaborator.fonte == SOURCE).all():
            db.delete(item)
            removed["colaboradores"] += 1
        db.flush()
        for item in db.query(User).filter(User.perfil == SOURCE).all():
            db.delete(item)
            removed["usuarios"] += 1
        db.commit()
        return removed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria ou remove dados ficticios isolados do piloto de IA.")
    parser.add_argument("--remove", action="store_true", help="Remove somente dados com o marcador desta seed.")
    args = parser.parse_args()
    backup = backup_database()
    result = remove() if args.remove else seed()
    print({"action": "remove" if args.remove else "seed", "backup": str(backup), "result": result})


if __name__ == "__main__":
    main()
