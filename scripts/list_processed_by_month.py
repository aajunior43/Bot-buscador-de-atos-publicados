import sqlite3
from collections import defaultdict
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

# Edições que passaram por IA (têm publicações com resumo_ia)
query = '''
SELECT DISTINCT e.id, e.data_publicacao, e.titulo
FROM edicoes e
JOIN publicacoes p ON p.edicao_id = e.id
WHERE p.resumo_ia IS NOT NULL AND p.resumo_ia != ''
ORDER BY e.data_publicacao DESC
'''
rows = conn.execute(query).fetchall()

print('=== Edições que passaram por OCR + IA (critério estrito) ===')
print(f'Total: {len(rows)}')
print()

# Agrupar por mês
por_mes = defaultdict(list)
for r in rows:
    mes = r['data_publicacao'][:7] if r['data_publicacao'] else 'sem data'
    por_mes[mes].append(f"{r['data_publicacao']} - {r['titulo']} (id={r['id']})")

for mes in sorted(por_mes.keys(), reverse=True):
    print(f'**{mes}** ({len(por_mes[mes])} edições):')
    for item in por_mes[mes]:
        print(f'  - {item}')
    print()

conn.close()