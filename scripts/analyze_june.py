import sqlite3
from collections import defaultdict
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== ANÁLISE DO MÊS 06/2026 (Junho 2026) ===')
print()

# Buscar todas as edições de junho 2026
rows = conn.execute('''
    SELECT e.id, e.titulo, e.data_publicacao, e.ocr_processado, e.tem_inaja,
           (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) as mencoes,
           (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) as pubs,
           (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id AND p.resumo_ia IS NOT NULL AND p.resumo_ia != '') as pubs_ia
    FROM edicoes e
    WHERE e.data_publicacao LIKE '2026-06%'
    ORDER BY e.data_publicacao DESC
''').fetchall()

print(f'Total de edições em junho/2026: {len(rows)}')
print()

# Resumo
ocr_count = sum(1 for r in rows if r['ocr_processado'])
mencoes_count = sum(1 for r in rows if r['mencoes'] > 0)
pubs_count = sum(1 for r in rows if r['pubs'] > 0)
pubs_ia_count = sum(1 for r in rows if r['pubs_ia'] > 0)
total_mencoes = sum(r['mencoes'] for r in rows)
total_pubs = sum(r['pubs'] for r in rows)
total_pubs_ia = sum(r['pubs_ia'] for r in rows)

print('Resumo:')
print(f'  Com OCR processado: {ocr_count}')
print(f'  Com menções a Inajá: {mencoes_count}')
print(f'  Com publicações: {pubs_count}')
print(f'  Com publicações refinadas por IA: {pubs_ia_count}')
print()
print(f'Total de menções: {total_mencoes}')
print(f'Total de publicações: {total_pubs}')
print(f'Total de publicações com IA: {total_pubs_ia}')
print()

print('Detalhamento por edição:')
for r in rows:
    status = []
    if r['ocr_processado']: status.append('OCR')
    if r['mencoes'] > 0: status.append(f"{r['mencoes']} menções")
    if r['pubs'] > 0: status.append(f"{r['pubs']} pubs")
    if r['pubs_ia'] > 0: status.append(f"{r['pubs_ia']} com IA")
    status_str = ' | '.join(status) if status else 'sem processamento'
    print(f"  {r['data_publicacao']} - {r['titulo']} (id={r['id']}): {status_str}")

conn.close()