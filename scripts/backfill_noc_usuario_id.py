"""Backfill controlado de `documentos.usuario_id` para registros historicos.

Muitos documentos foram sincronizados do MidiaSimples antes da Central NOC
existir e nunca tiveram `usuario_id` preenchido (contam como `sem_equipe`
hoje). Este comando tenta resolver o responsavel de cada um usando a MESMA
ordem definida no doc de handoff e implementada em
`src.services.noc.user_resolution.resolve_document_owner`:

1. id/`midiasimples_id` do tecnico no payload bruto da fonte;
2. e-mail do responsavel;
3. nome normalizado (so aceita quando ha exatamente 1 usuario ativo com
   aquele nome - nome ambiguo NUNCA e atribuido por semelhanca);
4. senao, fica `sem_equipe` (usuario_id continua None).

Por padrao roda em modo RELATORIO (dry-run): mostra quantos documentos
seriam resolvidos por id, por e-mail, por nome, quantos ficaram ambiguos e
quantos nao foram encontrados - sem gravar nada. So grava no banco quando
chamado com `--apply`.

Uso:
    python scripts/backfill_noc_usuario_id.py                # relatorio (dry-run)
    python scripts/backfill_noc_usuario_id.py --tipo rat      # so um tipo
    python scripts/backfill_noc_usuario_id.py --apply         # aplica de verdade
    python scripts/backfill_noc_usuario_id.py --apply --limit 500

Nunca atribui por equipe de quem rodou um checker nem por semelhanca fraca
de nome - documentos "ambiguo"/"nao_encontrado" continuam `sem_equipe` e
precisam de correcao manual.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.db_session import SessionLocal  # noqa: E402
from src.models.core import Document  # noqa: E402
from src.services.noc.user_resolution import resolve_document_owner  # noqa: E402


def _source_row(document: Document) -> dict[str, Any]:
    """Extrai a linha bruta da fonte externa a partir do payload salvo.

    Cobre os formatos ja usados hoje (`payload.dados.midiasimples` no RAT,
    `response_payload.row` como alternativa) e cai para o proprio `payload`
    quando nenhum dos dois existir, para nao deixar tipos futuros sem
    tentativa de resolucao.
    """
    payload = document.payload or {}
    dados = payload.get("dados") if isinstance(payload, dict) else None
    if isinstance(dados, dict) and isinstance(dados.get("midiasimples"), dict):
        return dados["midiasimples"]

    response_payload = document.response_payload or {}
    if isinstance(response_payload, dict) and isinstance(response_payload.get("row"), dict):
        return response_payload["row"]

    return payload if isinstance(payload, dict) else {}


def _fallback_name(document: Document) -> str | None:
    payload = document.payload or {}
    if not isinstance(payload, dict):
        return None
    dados = payload.get("dados")
    if isinstance(dados, dict):
        tecnico = dados.get("tecnico")
        if isinstance(tecnico, dict) and tecnico.get("nome"):
            return str(tecnico["nome"])
        rat = dados.get("rat")
        if isinstance(rat, dict) and rat.get("responsible"):
            return str(rat["responsible"])
    return None


def run_backfill(tipo: str | None, limit: int | None, apply: bool) -> dict[str, Any]:
    db = SessionLocal()
    counts: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    try:
        query = db.query(Document).filter(Document.usuario_id.is_(None))
        if tipo:
            query = query.filter(Document.tipo == tipo)
        query = query.order_by(Document.id.asc())
        if limit:
            query = query.limit(limit)

        documents = query.all()
        for document in documents:
            row = _source_row(document)
            resolution = resolve_document_owner(db, row, fallback_name=_fallback_name(document))
            counts[resolution.method] += 1
            details.append(
                {
                    "document_id": document.id,
                    "tipo": document.tipo,
                    "midiasimples_id": document.midiasimples_id,
                    "method": resolution.method,
                    "usuario_id": resolution.usuario_id,
                    "matched_name": resolution.matched_name,
                }
            )
            if apply and resolution.usuario_id is not None:
                document.usuario_id = resolution.usuario_id

        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {"counts": dict(counts), "total": len(details), "details": details, "applied": apply}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tipo", default=None, help="Restringe a um tipo de documento (ex.: rat, laudo).")
    parser.add_argument("--limit", type=int, default=None, help="Limite de documentos a processar nesta execucao.")
    parser.add_argument("--apply", action="store_true", help="Grava as resolucoes encontradas (default: so relatorio).")
    parser.add_argument("--verbose", action="store_true", help="Lista cada documento processado, nao so o resumo.")
    args = parser.parse_args()

    result = run_backfill(tipo=args.tipo, limit=args.limit, apply=args.apply)

    mode = "APLICADO" if result["applied"] else "RELATORIO (dry-run - nada foi gravado)"
    print(f"Backfill de usuario_id - {mode}")
    print(f"Documentos processados: {result['total']}")
    for method, count in sorted(result["counts"].items()):
        print(f"  {method}: {count}")

    if args.verbose:
        print()
        for item in result["details"]:
            print(
                f"  #{item['document_id']} ({item['tipo']}, midiasimples_id={item['midiasimples_id']}) "
                f"-> {item['method']}"
                + (f" usuario_id={item['usuario_id']}" if item["usuario_id"] else "")
                + (f" nome={item['matched_name']!r}" if item["matched_name"] else "")
            )

    if not result["applied"] and result["total"]:
        print()
        print("Nenhuma alteracao foi gravada. Revise o relatorio acima e rode de novo com --apply para aplicar.")


if __name__ == "__main__":
    main()
