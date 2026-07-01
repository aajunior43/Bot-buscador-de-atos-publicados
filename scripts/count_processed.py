import sqlite3
conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

print('=== Contagem no Banco de Dados ===')
total = conn.execute('SELECT COUNT(1) FROM edicoes').fetchone()[0]
ocr_flag = conn.execute('SELECT COUNT(1) FROM edicoes WHERE ocr_processado = 1').fetchone()[0]
com_texto = conn.execute("SELECT COUNT(1) FROM edicoes WHERE texto_extraido_path IS NOT NULL AND texto_extraido_path != ''").fetchone()[0]
com_mencoes = conn.execute('SELECT COUNT(DISTINCT edicao_id) FROM mencoes').fetchone()[0]
com_pubs = conn.execute('SELECT COUNT(DISTINCT edicao_id) FROM publicacoes').fetchone()[0]

print(f'Total edições cadastradas: {total}')
print(f'Com flag ocr_processado=1 (OCR marcado no DB): {ocr_flag}')
print(f'Com texto_extraido_path preenchido: {com_texto}')
print(f'Edições com menções: {com_mencoes}')
print(f'Edições com publicações: {com_pubs}')

conn.close()