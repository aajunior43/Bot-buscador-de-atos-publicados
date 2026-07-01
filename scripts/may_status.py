import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== CURRENT STATUS - MAIO 2026 ===')
rows = conn.execute('''
    SELECT e.data_publicacao, e.titulo,
           (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) as mencoes,
           (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) as pubs,
           (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id AND p.resumo_ia IS NOT NULL AND p.resumo_ia != '') as pubs_ia
    FROM edicoes e
    WHERE e.data_publicacao LIKE '2026-05%'
    ORDER BY e.data_publicacao ASC
''').fetchall()

for r in rows:
    status = 'OK' if r['pubs_ia'] > 0 else ('Fraca' if r['mencoes'] > 0 else 'Pendente')
    print(f"{r['data_publicacao']} - {r['titulo']}: {r['mencoes']} menções, {r['pubs']} pubs ({r['pubs_ia']} IA) - {status}")

conn.close()