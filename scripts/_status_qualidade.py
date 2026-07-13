# -*- coding: utf-8 -*-
"""Status rápido da camada de qualidade."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
import qualidade

database.init_db()
print("=== QUALIDADE ===")
r = qualidade.resumo_operacional()
for k, v in r.items():
    print(f"  {k}: {v}")
gaps = qualidade.listar_gaps_pendentes(10)
print(f"\nGaps pendentes: {len(gaps)}")
for g in gaps:
    print(
        f"  id={g['id']} {g.get('data_publicacao')} "
        f"sev={g.get('gap_severidade')} hits={g.get('gap_hits')} acao={g.get('gap_acao')}"
    )
with database.connect() as c:
    rows = c.execute(
        """
        SELECT confianca_nivel, COUNT(*) n FROM publicacoes
        WHERE confianca_nivel IS NOT NULL AND confianca_nivel != ''
        GROUP BY confianca_nivel
        """
    ).fetchall()
    print("\nNíveis:", [dict(x) for x in rows])
