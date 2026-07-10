# -*- coding: utf-8 -*-
"""Resumo por mês: edições, processadas, Inajá, publicações, menções.

Uso:
  python scripts/_resumo_mensal.py
  python scripts/_resumo_mensal.py --anos 2
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
    ap.add_argument("--anos", type=int, default=2, help="Quantos anos para trás")
    args = ap.parse_args()
    anos = max(1, min(10, args.anos))

    with database.connect() as c:
        rows = c.execute(
            """
            SELECT substr(e.data_publicacao, 1, 7) AS mes,
                   COUNT(*) AS edicoes,
                   SUM(CASE WHEN e.ocr_processado = 1 THEN 1 ELSE 0 END) AS ok,
                   SUM(CASE WHEN e.ocr_processado = 0 THEN 1 ELSE 0 END) AS pend,
                   SUM(CASE WHEN e.tem_inaja = 1 THEN 1 ELSE 0 END) AS inaja,
                   (SELECT COUNT(*) FROM publicacoes p
                      JOIN edicoes e2 ON e2.id = p.edicao_id
                      WHERE substr(e2.data_publicacao,1,7) = substr(e.data_publicacao,1,7)
                   ) AS pubs,
                   (SELECT COUNT(*) FROM mencoes m
                      JOIN edicoes e3 ON e3.id = m.edicao_id
                      WHERE substr(e3.data_publicacao,1,7) = substr(e.data_publicacao,1,7)
                   ) AS men
            FROM edicoes e
            WHERE e.data_publicacao IS NOT NULL AND e.data_publicacao != ''
              AND e.data_publicacao >= date('now', ?)
            GROUP BY substr(e.data_publicacao, 1, 7)
            ORDER BY mes DESC
            """,
            (f"-{anos * 12} months",),
        ).fetchall()

    print(f"\n  === Resumo mensal (últimos ~{anos} ano(s)) ===\n")
    print(
        f"  {'Mês':8}  {'Edic':>5}  {'OK':>5}  {'Pend':>5}  "
        f"{'Inaja':>5}  {'Pubs':>5}  {'Men':>5}"
    )
    print("  " + "-" * 52)
    if not rows:
        print("  (sem dados)")
        return 0
    for r in rows:
        print(
            f"  {r['mes'] or '????-??':8}  {r['edicoes']:5}  {r['ok'] or 0:5}  "
            f"{r['pend'] or 0:5}  {r['inaja'] or 0:5}  "
            f"{r['pubs'] or 0:5}  {r['men'] or 0:5}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
