# -*- coding: utf-8 -*-
"""Lista menções de um mês (AAAA-MM).

Uso:
  python scripts/_listar_mencoes.py 2026-07
  python scripts/_listar_mencoes.py 2026-06 --limite 50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mes", help="AAAA-MM")
    ap.add_argument("--limite", type=int, default=40)
    args = ap.parse_args()
    mes = args.mes.strip()
    if len(mes) != 7 or mes[4] != "-":
        print("Formato inválido. Use AAAA-MM")
        return 2
    lim = max(1, min(200, args.limite))

    with database.connect() as c:
        rows = c.execute(
            """
            SELECT m.pagina, m.termo_encontrado, m.trecho,
                   e.data_publicacao, e.titulo
            FROM mencoes m
            JOIN edicoes e ON e.id = m.edicao_id
            WHERE e.data_publicacao LIKE ?
            ORDER BY e.data_publicacao, m.pagina, m.id
            """,
            (f"{mes}%",),
        ).fetchall()

    print(f"\n  TOTAL: {len(rows)} menções em {mes}\n")
    if not rows:
        print("  (nenhuma)\n")
        return 0

    for i, r in enumerate(rows[:lim], 1):
        trecho = (r["trecho"] or "").replace("\n", " ").strip()
        if len(trecho) > 160:
            trecho = trecho[:160] + "…"
        print(
            f"  {i:2}. [{r['data_publicacao']}] p.{r['pagina']} · "
            f"{r['termo_encontrado']}"
        )
        print(f"      {trecho}")
        print()
    if len(rows) > lim:
        print(f"  … e mais {len(rows) - lim} (use --limite N)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
