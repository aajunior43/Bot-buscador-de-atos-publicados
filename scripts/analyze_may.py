import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== EDIÇÕES DE MAIO 2026 (mês 5) ===')
rows = conn.execute('''
    SELECT e.id, e.titulo, e.data_publicacao, e.ocr_processado,
           (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) as mencoes,
           (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) as pubs,
           (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id AND p.resumo_ia IS NOT NULL AND p.resumo_ia != '') as pubs_ia
    FROM edicoes e
    WHERE e.data_publicacao LIKE '2026-05%'
    ORDER BY e.data_publicacao ASC
''').fetchall()

print(f'Total de edições em maio/2026: {len(rows)}')
print()

for r in rows:
    status = []
    if r['ocr_processado']: status.append('OCR')
    if r['mencoes'] > 0: status.append(f"{r['mencoes']} menções")
    if r['pubs'] > 0: status.append(f"{r['pubs']} pubs")
    if r['pubs_ia'] > 0: status.append(f"{r['pubs_ia']} com IA")
    status_str = ' | '.join(status) if status else 'sem processamento'
    print(f"{r['data_publicacao']} - {r['titulo']} (id={r['id']}): {status_str}")

conn.close()