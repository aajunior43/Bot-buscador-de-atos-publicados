import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== Edições com OCR (pelo flag no banco) ===')
ocr_db = conn.execute('SELECT COUNT(1) FROM edicoes WHERE ocr_processado=1').fetchone()[0]
print(f'Com ocr_processado=1: {ocr_db}')

print()
print('=== Edições que passaram pela IA (têm publicações com resumo_ia) ===')
ia_count = conn.execute("SELECT COUNT(DISTINCT edicao_id) FROM publicacoes WHERE resumo_ia IS NOT NULL AND resumo_ia != ''").fetchone()[0]
print(f'Edições com publicações refinadas por IA: {ia_count}')

print()
print('=== Edições com OCR + IA (interseção) ===')
query = """
SELECT COUNT(DISTINCT e.id) 
FROM edicoes e
JOIN publicacoes p ON p.edicao_id = e.id
WHERE e.ocr_processado = 1 
  AND p.resumo_ia IS NOT NULL 
  AND p.resumo_ia != ''
"""
both = conn.execute(query).fetchone()[0]
print(f'Edições com OCR (flag) E refinadas por IA: {both}')

print()
print('=== Total de publicações com resumo_ia ===')
total_ia = conn.execute("SELECT COUNT(1) FROM publicacoes WHERE resumo_ia IS NOT NULL AND resumo_ia != ''").fetchone()[0]
print(f'Total de publicações com resumo_ia: {total_ia}')

conn.close()