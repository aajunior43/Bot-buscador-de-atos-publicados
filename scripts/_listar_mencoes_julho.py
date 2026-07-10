# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()
with database.connect() as c:
    rows = c.execute(
        """
        SELECT m.id, m.pagina, m.termo_encontrado, m.trecho, e.data_publicacao, e.titulo
        FROM mencoes m
        JOIN edicoes e ON e.id = m.edicao_id
        WHERE e.data_publicacao LIKE '2026-07%'
        ORDER BY e.data_publicacao, m.pagina, m.id
        """
    ).fetchall()

print(f"TOTAL: {len(rows)} menções (jul/2026)\n")
for i, r in enumerate(rows, 1):
    trecho = (r["trecho"] or "").replace("\n", " ").strip()
    if len(trecho) > 200:
        trecho = trecho[:200] + "..."
    print(f"{i:2}. pág.{r['pagina']} · termo: {r['termo_encontrado']}")
    print(f"    {trecho}")
    print()
