import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

rows = conn.execute('''
    SELECT id, titulo, data_publicacao, url, 
           (SELECT COUNT(*) FROM mencoes WHERE edicao_id = e.id) as mencoes,
           (SELECT COUNT(*) FROM publicacoes WHERE edicao_id = e.id) as pubs,
           (SELECT COUNT(*) FROM publicacoes WHERE edicao_id = e.id AND resumo_ia IS NOT NULL) as pubs_ia
    FROM edicoes e
    WHERE data_publicacao LIKE '2026-05%'
    ORDER BY data_publicacao ASC
''').fetchall()

print('=== May 2026 editions needing processing ===')
for r in rows:
    print(f"id={r['id']}: {r['data_publicacao']} - {r['titulo']}")
    print(f"  mencoes={r['mencoes']}, pubs={r['pubs']}, pubs_ia={r['pubs_ia']}")
    print(f"  url={r['url']}")
    print()

conn.close()