import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== STATUS ATUAL ===')
print()

total = conn.execute('SELECT COUNT(1) FROM edicoes').fetchone()[0]
print(f'Total de edições cadastradas no banco: {total}')

ocr_flag = conn.execute('SELECT COUNT(1) FROM edicoes WHERE ocr_processado = 1').fetchone()[0]
print(f'Com flag ocr_processado=1: {ocr_flag}')

com_texto = conn.execute("SELECT COUNT(1) FROM edicoes WHERE texto_extraido_path IS NOT NULL AND texto_extraido_path != ''").fetchone()[0]
print(f'Com texto_extraido_path preenchido: {com_texto}')

mencoes = conn.execute('SELECT COUNT(DISTINCT edicao_id) FROM mencoes').fetchone()[0]
print(f'Com menções registradas: {mencoes}')

pubs = conn.execute('SELECT COUNT(DISTINCT edicao_id) FROM publicacoes').fetchone()[0]
print(f'Com publicações estruturadas: {pubs}')

conn.close()