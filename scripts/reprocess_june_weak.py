import logging
import sys
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s', stream=sys.stdout)

import database
from scraper import Edicao
from downloader import baixar_edicao
from ocr_processor import extrair_texto
from detector import detectar

# Weak June 2026 editions that need reprocessing (OCR but no good IA results)
editions = [
    (33448, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-02-06-2026.pdf', '02-06-2026'),
    (33447, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-04-06-2026.pdf', '04-06-2026'),
    (33446, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-07-06-2026.pdf', '07-06-2026'),
    (33445, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-09-06-2026.pdf', '09-06-2026'),
    (33444, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-11-06-2026.pdf', '11-06-2026'),
    (33440, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-19-06-2026.pdf', '19-06-2026'),
    (33435, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-30-06-2026-.pdf', '30-06-2026'),
]

for ed_id, url, titulo in editions:
    data_pub = '2026-06-' + titulo.split('-')[0]
    print(f'\\n=== REPROCESSING {titulo} (id={ed_id}) ===')

    # Clean previous results
    with database.connect() as conn:
        conn.execute('DELETE FROM mencoes WHERE edicao_id=?', (ed_id,))
        conn.execute('DELETE FROM publicacoes WHERE edicao_id=?', (ed_id,))
        conn.execute('UPDATE edicoes SET tem_inaja=0 WHERE id=?', (ed_id,))

    edicao = Edicao(url=url, titulo=titulo, data_publicacao=data_pub)

    print('Download...')
    download = baixar_edicao(edicao)

    print('OCR force...')
    def prog(m): print('  ', m)
    ocr = extrair_texto(download.caminho, force_ocr=True, on_progress=prog)
    print(f'OCR done: {len(ocr.paginas)} pages')

    print('Detect + IA...')
    res = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
    print(f'Result: encontrado={res.encontrado}, trechos={len(res.trechos)}, pubs={len(res.publicacoes)}')

    print('Persist...')
    database.insert_mencoes(download.edicao_id, res.mencoes_db)
    database.insert_publicacoes(download.edicao_id, res.publicacoes)
    database.salvar_arquivos_atos_locais(ocr.texto_path, res.publicacoes)
    database.update_ocr(download.edicao_id, ocr.texto_path, res.encontrado)

print('\\n=== ALL DONE ===')
