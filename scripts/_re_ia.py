# -*- coding: utf-8 -*-
"""Re-roda refinamento IA em publicações fracas.

Uso:
  python scripts/_re_ia.py
  python scripts/_re_ia.py --mes 2026-07
  python scripts/_re_ia.py --limite 10
  python scripts/_re_ia.py --so-sem-resumo
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
import qualidade
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
        help="Só pubs sem resumo_ia (filtro extra após listar_candidatas)",
    )
    args = ap.parse_args()
    lim = max(1, min(100, args.limite))

    if not ia_disponivel():
        print("IA indisponível (chave/flag).")
        return 2

    database.init_db()
    rows = qualidade.listar_candidatas_re_ia(lim * 2, mes=args.mes.strip())
    if args.so_sem_resumo:
        rows = [r for r in rows if not (r.get("resumo_ia") or "").strip()]
    rows = rows[:lim]

    print(f"\n  Publicações candidatas: {len(rows)}\n")
    if not rows:
        print("  Nada a refinar.\n")
        return 0

    reset_ai_call_counter()
    refinadas, stats = refinar_publicacoes(rows)
    log.info("stats=%s", stats)

    updated = 0
    for p in refinadas:
        if not p or not p.get("id"):
            continue
        try:
            data_ed = p.get("data_publicacao")
            if not data_ed and p.get("edicao_id"):
                with database.connect() as c:
                    row = c.execute(
                        "SELECT data_publicacao FROM edicoes WHERE id=?",
                        (p["edicao_id"],),
                    ).fetchone()
                    data_ed = row["data_publicacao"] if row else None
            p = qualidade.aplicar_pos_re_ia(p, data_edicao=data_ed)
            database.update_publicacao_ia(p, registrar_tentativa=True)
            updated += 1
        except Exception:
            log.exception("falha update id=%s", p.get("id"))

    print(f"  Atualizadas: {updated}  stats={stats}")
    for p in (refinadas or [])[:15]:
        if not p:
            continue
        print(
            f"    · {p.get('tipo') or '?'} {p.get('numero') or ''} | "
            f"conf={p.get('confianca_nivel') or '—'} | "
            f"{(p.get('resumo_ia') or '')[:60]}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
