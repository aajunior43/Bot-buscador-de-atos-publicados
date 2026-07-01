import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row
print('=== Updated May Fraca ===')
ids = [35512, 35508, 33638]
for eid in ids:
    r = conn.execute('SELECT data_publicacao, titulo, (SELECT COUNT(*) FROM mencoes WHERE edicao_id=?) as mencoes, (SELECT COUNT(*) FROM publicacoes WHERE edicao_id=?) as pubs, (SELECT COUNT(*) FROM publicacoes WHERE edicao_id=? AND resumo_ia IS NOT NULL AND resumo_ia != "") as pubs_ia FROM edicoes WHERE id=?', (eid,eid,eid,eid)).fetchone()
    print(f"{r['data_publicacao']} {r['titulo']}: {r['mencoes']} menções, {r['pubs']} pubs, {r['pubs_ia']} IA")
conn.close()