import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row
print('=== Status atual das edições fracas de junho ===')
ids = [33448, 33447, 33446, 33445, 33444, 33440, 33435]
for eid in ids:
    r = conn.execute('SELECT id, titulo, data_publicacao, ocr_processado, (SELECT COUNT(*) FROM mencoes WHERE edicao_id=edicoes.id) as mencoes, (SELECT COUNT(*) FROM publicacoes WHERE edicao_id=edicoes.id) as pubs, (SELECT COUNT(*) FROM publicacoes WHERE edicao_id=edicoes.id AND resumo_ia IS NOT NULL AND resumo_ia != \"\") as pubs_ia FROM edicoes WHERE id=?', (eid,)).fetchone()
    if r:
        print(f"{r['data_publicacao']} (id={r['id']}): mencoes={r['mencoes']}, pubs={r['pubs']}, pubs_ia={r['pubs_ia']}, ocr={r['ocr_processado']}")
conn.close()