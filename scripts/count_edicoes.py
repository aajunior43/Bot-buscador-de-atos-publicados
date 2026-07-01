import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== Contagens no banco ===')
total = conn.execute('SELECT COUNT(1) FROM edicoes').fetchone()[0]
ocr = conn.execute('SELECT COUNT(1) FROM edicoes WHERE ocr_processado=1').fetchone()[0]
tem_inaja = conn.execute('SELECT COUNT(1) FROM edicoes WHERE tem_inaja=1').fetchone()[0]

com_mencoes = conn.execute('SELECT COUNT(DISTINCT edicao_id) FROM mencoes').fetchone()[0]
com_publicacoes = conn.execute('SELECT COUNT(DISTINCT edicao_id) FROM publicacoes').fetchone()[0]

com_texto = conn.execute("SELECT COUNT(1) FROM edicoes WHERE texto_extraido_path IS NOT NULL AND texto_extraido_path != ''").fetchone()[0]

print(f'Total de edições no DB: {total}')
print(f'Edições com OCR processado: {ocr}')
print(f'Edições marcadas com tem_inaja=1: {tem_inaja}')
print(f'Edições com pelo menos 1 menção: {com_mencoes}')
print(f'Edições com pelo menos 1 publicação: {com_publicacoes}')
print(f'Edições com texto_extraido_path preenchido: {com_texto}')

conn.close()