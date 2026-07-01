from pathlib import Path
import sqlite3

conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

# Edições que têm publicações com resumo_ia (passaram pela IA)
ia_edicoes = set()
for r in conn.execute("SELECT DISTINCT edicao_id FROM publicacoes WHERE resumo_ia IS NOT NULL AND resumo_ia != ''").fetchall():
    ia_edicoes.add(r['edicao_id'])

print(f'Edições com IA (resumo_ia): {len(ia_edicoes)}')

# Todas com .ocr.json
ocr_files = list(Path('edicoes').rglob('*.ocr.json'))
print(f'Total com .ocr.json: {len(ocr_files)}')

# Mapear para DB
all_with_ocr = []
for f in ocr_files:
    base = f.stem.replace('.ocr', '')
    row = conn.execute('SELECT id, titulo, data_publicacao, ocr_processado FROM edicoes WHERE titulo LIKE ? OR caminho_local LIKE ?', (f'%{base}%', f'%{base}%')).fetchone()
    if row:
        all_with_ocr.append({'id': row['id'], 'titulo': row['titulo'], 'data': row['data_publicacao'], 'flag': row['ocr_processado'], 'file': base})
    else:
        all_with_ocr.append({'id': None, 'titulo': base, 'data': None, 'flag': 0, 'file': base})

# As que têm .ocr.json mas NÃO têm IA
need_reprocess = [e for e in all_with_ocr if e['id'] not in ia_edicoes]
print(f'Com .ocr.json mas sem IA: {len(need_reprocess)}')

print('\nExemplos que precisam de atenção (com OCR mas sem IA):')
for e in need_reprocess[:10]:
    print(f"  {e['data'] or e['titulo']} (id={e['id']}, flag={e['flag']})")

print(f'\nTotal que você provavelmente quer reprocessar: {len(need_reprocess)}')

conn.close()