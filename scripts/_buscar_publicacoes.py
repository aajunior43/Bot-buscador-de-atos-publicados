# -*- coding: utf-8 -*-
"""Busca publicações por texto (órgão, tipo, número, assunto, resumo).

Uso:
  python scripts/_buscar_publicacoes.py aditivo
  python scripts/_buscar_publicacoes.py "prefeitura" -n 20
  python scripts/_buscar_publicacoes.py 04/2026 --mes 2026-07
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
    ap.add_argument("termo", help="Texto a buscar")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--mes", default="", help="Filtrar AAAA-MM")
    args = ap.parse_args()
    termo = (args.termo or "").strip()
    if not termo:
        print("Informe um termo.")
        return 2
    n = max(1, min(100, args.n))
    like = f"%{termo}%"
    mes = (args.mes or "").strip()

    sql = """
        SELECT p.id, p.tipo, p.numero, p.orgao, p.valor, p.importancia,
               p.resumo_ia, p.assunto, e.data_publicacao, p.pagina
        FROM publicacoes p
        JOIN edicoes e ON e.id = p.edicao_id
        WHERE (
            p.tipo LIKE ? OR p.numero LIKE ? OR p.orgao LIKE ?
            OR p.assunto LIKE ? OR p.resumo_ia LIKE ? OR p.valor LIKE ?
        )
    """
    params: list[object] = [like, like, like, like, like, like]
    if mes:
        sql += " AND e.data_publicacao LIKE ?"
        params.append(f"{mes}%")
    sql += " ORDER BY e.data_publicacao DESC, p.id DESC LIMIT ?"
    params.append(n)

    with database.connect() as c:
        rows = c.execute(sql, params).fetchall()

    print(f"\n  Busca: «{termo}»  →  {len(rows)} resultado(s)\n")
    if not rows:
        print("  (nada encontrado)\n")
        return 0
    for i, r in enumerate(rows, 1):
        resumo = (r["resumo_ia"] or r["assunto"] or "—").replace("\n", " ")
        if len(resumo) > 110:
            resumo = resumo[:110] + "…"
        print(
            f"  {i:2}. [{r['data_publicacao']}] {r['tipo'] or '?'} "
            f"{r['numero'] or ''}  ★{r['importancia'] or '—'}"
        )
        print(f"      {(r['orgao'] or '—')[:55]}  {r['valor'] or ''}")
        print(f"      {resumo}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
