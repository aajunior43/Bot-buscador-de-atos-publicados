import logging
import sys
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s', stream=sys.stdout)

import database
from scraper import Edicao
from downloader import baixar_edicao
from ocr_processor import extrair_texto
from detector import detectar

ed_id = 33441
url = 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-18-06-2026.pdf'
titulo = '18-06-2026'
data_pub = '2026-06-18'

print('=== PROCESSING NEXT: 18-06-2026 ===')
print(f'Edition id={ed_id}')

# Clean previous
with database.connect() as conn:
    conn.execute('DELETE FROM mencoes WHERE edicao_id=?', (ed_id,))
    conn.execute('DELETE FROM publicacoes WHERE edicao_id=?', (ed_id,))
    conn.execute('UPDATE edicoes SET tem_inaja=0 WHERE id=?', (ed_id,))

edicao = Edicao(url=url, titulo=titulo, data_publicacao=data_pub)

print('Download...')
download = baixar_edicao(edicao)
print(f'Path: {download.caminho}')

print('OCR force...')
def prog(m): print('  ', m)
ocr = extrair_texto(download.caminho, force_ocr=True, on_progress=prog)
print(f'OCR: {len(ocr.paginas)} pages, {len(ocr.texto_completo)} chars')

print('Detect...')
res = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
print(f'encontrado={res.encontrado}, trechos={len(res.trechos)}, pubs={len(res.publicacoes)}')

print('Persist...')
database.insert_mencoes(download.edicao_id, res.mencoes_db)
database.insert_publicacoes(download.edicao_id, res.publicacoes)
database.salvar_arquivos_atos_locais(ocr.texto_path, res.publicacoes)
database.update_ocr(download.edicao_id, ocr.texto_path, res.encontrado)

print('DONE')
print(f'encontrado={res.encontrado}')
if res.publicacoes:
    for p in res.publicacoes:
        print('  ', p.get('orgao'), p.get('tipo'), p.get('numero'), (p.get('assunto') or '')[:60])
