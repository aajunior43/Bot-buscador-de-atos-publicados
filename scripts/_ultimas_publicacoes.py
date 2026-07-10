# -*- coding: utf-8 -*-
"""Lista as últimas publicações detectadas."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=15, help="Quantidade (padrão 15)")
    ap.add_argument("--mes", default="", help="Filtrar AAAA-MM")
    ap.add_argument(
        "--min-imp",
        type=int,
        default=0,
        help="Importância mínima (0 = todas)",
    )
    args = ap.parse_args()
    n = max(1, min(100, args.n))
    mes = (args.mes or "").strip()
    min_imp = max(0, args.min_imp)

    sql = """
        SELECT p.id, p.tipo, p.numero, p.orgao, p.valor, p.importancia,
               p.resumo_ia, p.assunto, e.data_publicacao, e.titulo, p.pagina
        FROM publicacoes p
        JOIN edicoes e ON e.id = p.edicao_id
        WHERE 1=1
    """
    params: list[object] = []
    if mes:
        sql += " AND e.data_publicacao LIKE ?"
        params.append(f"{mes}%")
    if min_imp > 0:
        sql += " AND CAST(COALESCE(p.importancia,0) AS INTEGER) >= ?"
        params.append(min_imp)
    sql += " ORDER BY e.data_publicacao DESC, p.id DESC LIMIT ?"
    params.append(n)

    with database.connect() as c:
        rows = c.execute(sql, params).fetchall()

    filtro = []
    if mes:
        filtro.append(f"mes={mes}")
    if min_imp:
        filtro.append(f"imp>={min_imp}")
    extra = f" ({', '.join(filtro)})" if filtro else ""
    print(f"\n  Últimas {len(rows)} publicações{extra}\n")
    if not rows:
        print("  (nenhuma)\n")
        return
    for i, r in enumerate(rows, 1):
        resumo = (r["resumo_ia"] or r["assunto"] or "—").replace("\n", " ")
        if len(resumo) > 100:
            resumo = resumo[:100] + "…"
        imp = r["importancia"] or "—"
        print(
            f"  {i:2}. [{r['data_publicacao']}] {r['tipo'] or '?'} "
            f"{r['numero'] or ''}  ★{imp}  p.{r['pagina'] or '?'}"
        )
        print(f"      {(r['orgao'] or '—')[:50]}  {r['valor'] or ''}")
        print(f"      {resumo}")
        print()


if __name__ == "__main__":
    main()
