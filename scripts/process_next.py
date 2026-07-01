import logging
import sys
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s', stream=sys.stdout)

import database
from scraper import Edicao
from downloader import baixar_edicao
from ocr_processor import extrair_texto
from detector import detectar

ed_id = 33438
url = 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-23-06-2026-.pdf'
titulo = '23-06-2026'
data_pub = '2026-06-23'

print('=== PROCESSING NEXT EDITION (23-06-2026) ===')
print(f'Edition: id={ed_id} | {titulo} | {data_pub}')

# Clean previous
print('Cleaning previous detections...')
with database.connect() as conn:
    conn.execute('DELETE FROM mencoes WHERE edicao_id=?', (ed_id,))
    conn.execute('DELETE FROM publicacoes WHERE edicao_id=?', (ed_id,))
    conn.execute('UPDATE edicoes SET tem_inaja=0 WHERE id=?', (ed_id,))
print('Cleaned.')

edicao = Edicao(url=url, titulo=titulo, data_publicacao=data_pub)

print('--- Download ---')
download = baixar_edicao(edicao)
print(f'Path: {download.caminho}')

print('--- OCR (force_ocr=True) ---')
def on_progress(msg):
    print('  ', msg)
ocr = extrair_texto(download.caminho, force_ocr=True, on_progress=on_progress)
print(f'OCR: {len(ocr.paginas)} pages, {len(ocr.texto_completo)} chars')

print('--- Detection ---')
resultado = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
print(f'encontrado={resultado.encontrado}')
print(f'paginas: {resultado.paginas_com_mencao}')
print(f'termos: {resultado.termos_encontrados}')
print(f'trechos: {len(resultado.trechos)}')
print(f'publicacoes: {len(resultado.publicacoes)}')

print('--- Persist ---')
database.insert_mencoes(download.edicao_id, resultado.mencoes_db)
database.insert_publicacoes(download.edicao_id, resultado.publicacoes)
database.salvar_arquivos_atos_locais(ocr.texto_path, resultado.publicacoes)
database.update_ocr(download.edicao_id, ocr.texto_path, resultado.encontrado)

print('=== COMPLETE ===')
print(f'encontrado={resultado.encontrado}')
if resultado.publicacoes:
    for p in resultado.publicacoes:
        print('  ', p.get('orgao'), p.get('tipo'), p.get('numero'), '|', (p.get('assunto') or '')[:70])