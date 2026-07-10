# -*- coding: utf-8 -*-
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()
with database.connect() as c:
    rows = c.execute(
        """
        SELECT e.id, e.data_publicacao, e.titulo, e.ocr_processado, e.tem_inaja,
               e.caminho_local,
               (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) AS n_pub,
               (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) AS n_men
        FROM edicoes e
        WHERE e.data_publicacao LIKE '2026-07%'
        ORDER BY e.data_publicacao
        """
    ).fetchall()

print(f"EDIÇÕES NO BANCO (jul/2026): {len(rows)}")
for r in rows:
    path = Path(r["caminho_local"] or "")
    print(
        f"  {r['data_publicacao']} id={r['id']} ocr={r['ocr_processado']} "
        f"inaja={r['tem_inaja']} pubs={r['n_pub']} men={r['n_men']} "
        f"pdf={path.exists() if r['caminho_local'] else False} "
        f"ocrj={path.with_suffix('.ocr.json').exists() if r['caminho_local'] else False}"
    )

pasta = Path("edicoes/2026/07")
pdfs = sorted(pasta.glob("*.pdf")) if pasta.exists() else []
print(f"PDFs em edicoes/2026/07: {len(pdfs)}")
for p in pdfs:
    print(f"  {p.name}")
