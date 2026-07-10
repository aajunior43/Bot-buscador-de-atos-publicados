# -*- coding: utf-8 -*-
"""Re-roda refinamento IA em publicações fracas (sem resumo / sem valor).

Uso:
  python scripts/_re_ia.py
  python scripts/_re_ia.py --mes 2026-07
  python scripts/_re_ia.py --limite 10
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from ai_processor import ia_disponivel, refinar_publicacoes, reset_ai_call_counter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("re_ia")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes", default="")
    ap.add_argument("--limite", type=int, default=20)
    ap.add_argument(
        "--so-sem-resumo",
        action="store_true",
        help="Só pubs sem resumo_ia",
    )
    args = ap.parse_args()
    lim = max(1, min(100, args.limite))

    if not ia_disponivel():
        print("IA indisponível (chave/flag).")
        return 2

    database.init_db()
    sql = """
        SELECT p.*, e.data_publicacao
        FROM publicacoes p
        JOIN edicoes e ON e.id = p.edicao_id
        WHERE (
            p.resumo_ia IS NULL OR trim(p.resumo_ia) = ''
    """
    if not args.so_sem_resumo:
        sql += " OR p.valor IS NULL OR trim(p.valor) = ''"
    sql += ")"
    params: list = []
    if args.mes.strip():
        sql += " AND e.data_publicacao LIKE ?"
        params.append(f"{args.mes.strip()}%")
    sql += " ORDER BY e.data_publicacao DESC, p.id DESC LIMIT ?"
    params.append(lim)

    with database.connect() as c:
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]

    print(f"\n  Publicações candidatas: {len(rows)}\n")
    if not rows:
        print("  Nada a refinar.\n")
        return 0

    pubs = []
    for r in rows:
        trecho = (r.get("trecho") or r.get("assunto") or r.get("texto_corrigido") or "").strip()
        if not trecho:
            continue
        pubs.append(
            {
                "id": r["id"],
                "edicao_id": r["edicao_id"],
                "tipo": r.get("tipo"),
                "numero": r.get("numero"),
                "orgao": r.get("orgao"),
                "assunto": r.get("assunto"),
                "valor": r.get("valor"),
                "resumo_ia": r.get("resumo_ia"),
                "pagina": r.get("pagina"),
                "trecho": trecho,
                "importancia": r.get("importancia"),
            }
        )

    if not pubs:
        print("  Nenhuma com trecho para reenviar à IA.\n")
        return 0

    reset_ai_call_counter()
    refinadas, stats = refinar_publicacoes(pubs)
    log.info("stats=%s", stats)

    updated = 0
    for p in refinadas:
        if not p or not p.get("id"):
            continue
        try:
            database.update_publicacao_ia(p)
            updated += 1
        except Exception:
            log.exception("falha update id=%s", p.get("id"))
            # fallback mínimo
            with database.connect() as c:
                c.execute(
                    """
                    UPDATE publicacoes SET
                      resumo_ia = COALESCE(?, resumo_ia),
                      valor = COALESCE(?, valor),
                      numero = COALESCE(?, numero),
                      orgao = COALESCE(?, orgao),
                      tipo = COALESCE(?, tipo)
                    WHERE id = ?
                    """,
                    (
                        p.get("resumo_ia"),
                        p.get("valor"),
                        p.get("numero"),
                        p.get("orgao"),
                        p.get("tipo"),
                        p["id"],
                    ),
                )
                updated += 1

    print(f"  Atualizadas: {updated}  stats={stats}")
    for p in (refinadas or [])[:15]:
        if not p:
            continue
        print(
            f"    · {p.get('tipo') or '?'} {p.get('numero') or ''} | "
            f"{(p.get('resumo_ia') or '')[:70]}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
