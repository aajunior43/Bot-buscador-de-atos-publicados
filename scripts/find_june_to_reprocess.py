import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== Edições de junho 2026 que precisam de reprocessamento ===')
rows = conn.execute('''
    SELECT id, titulo, data_publicacao, url, caminho_local, ocr_processado,
           (SELECT COUNT(*) FROM mencoes WHERE edicao_id = edicoes.id) as mencoes,
           (SELECT COUNT(*) FROM publicacoes WHERE edicao_id = edicoes.id) as pubs,
           (SELECT COUNT(*) FROM publicacoes WHERE edicao_id = edicoes.id AND resumo_ia IS NOT NULL AND resumo_ia != '') as pubs_ia
    FROM edicoes
    WHERE data_publicacao LIKE '2026-06%'
    ORDER BY data_publicacao ASC
''').fetchall()

to_reprocess = []
for r in rows:
    needs = False
    if r['ocr_processado'] == 0:
        needs = True
    elif r['pubs_ia'] == 0 and r['pubs'] > 0:
        needs = True
    elif r['mencoes'] == 0 and r['ocr_processado'] == 1:
        needs = True  # weak ones

    if needs:
        to_reprocess.append(r)
        print(f"id={r['id']}, {r['data_publicacao']} - {r['titulo']}")
        print(f"  url: {r['url']}")
        print(f"  mencoes={r['mencoes']}, pubs={r['pubs']}, pubs_ia={r['pubs_ia']}, ocr={r['ocr_processado']}")
        print()

print(f'Total a reprocessar: {len(to_reprocess)}')
conn.close()