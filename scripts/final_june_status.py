import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== STATUS FINAL - JUNHO 2026 (após reprocessamento) ===')
print()

rows = conn.execute('''
    SELECT e.data_publicacao, e.titulo, e.id,
           (SELECT COUNT(*) FROM mencoes WHERE edicao_id = e.id) as mencoes,
           (SELECT COUNT(*) FROM publicacoes WHERE edicao_id = e.id) as pubs,
           (SELECT COUNT(*) FROM publicacoes WHERE edicao_id = e.id AND resumo_ia IS NOT NULL AND resumo_ia != '') as pubs_ia
    FROM edicoes e
    WHERE e.data_publicacao LIKE '2026-06%'
    ORDER BY e.data_publicacao ASC
''').fetchall()

print('Data       | Menções | Pubs | Pubs c/ IA | Status')
print('-' * 55)
for r in rows:
    status = 'OK' if r['pubs_ia'] > 0 else ('Fraca' if r['mencoes'] > 0 else 'Sem conteúdo')
    print(f"{r['data_publicacao']} | {r['mencoes']:7} | {r['pubs']:4} | {r['pubs_ia']:8} | {status}")

print()
total_m = sum(r['mencoes'] for r in rows)
total_p = sum(r['pubs'] for r in rows)
total_ia = sum(r['pubs_ia'] for r in rows)
print(f'Totais: {total_m} menções | {total_p} publicações | {total_ia} com IA')

conn.close()