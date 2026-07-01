import logging
import sys
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s', stream=sys.stdout)

import database
from scraper import Edicao
from downloader import baixar_edicao
from ocr_processor import extrair_texto
from detector import detectar

# May 2026 editions (all need full reprocess as none have pubs/IA yet)
editions = [
    (35521, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-03-05-2026.pdf', '03-05-2026', '2026-05-03'),
    (35520, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-05-05-2026.pdf', '05-05-2026', '2026-05-05'),
    (35519, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-07-05-2026.pdf', '07-05-2026', '2026-05-07'),
    (35518, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-10-05-2026.pdf', '10-05-2026', '2026-05-10'),
    (35517, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-12-05-2026.pdf', '12-05-2026', '2026-05-12'),
    (35516, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-14-05-2026.pdf', '14-05-2026', '2026-05-14'),
    (35515, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-15-05-2026.pdf', '15-05-2026', '2026-05-15'),
    (35514, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-17-05-2026.pdf', '17-05-2026', '2026-05-17'),
    (35513, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-19-05-2026.pdf', '19-05-2026', '2026-05-19'),
    (35512, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-21-05-2026.pdf', '21-05-2026', '2026-05-21'),
    (35511, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-22-05-2026.pdf', '22-05-2026', '2026-05-22'),
    (35510, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-24-05-2026.pdf', '24-05-2026', '2026-05-24'),
    (35509, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-26-05-2026.pdf', '26-05-2026', '2026-05-26'),
    (35508, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-28-05-2026.pdf', '28-05-2026', '2026-05-28'),
    (33638, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/06/Jornal-O-Regional-30-05-20266.pdf', '30-05-2026', '2026-05-30'),  # note weird path
    (33637, 'https://www.oregionaljornal.com.br/wp-content/uploads/2026/05/Jornal-O-Regional-31-05-2026.pdf', '31-05-2026', '2026-05-31'),
]

for ed_id, url, titulo, data_pub in editions:
    print(f'\n=== REPROCESSING {titulo} (id={ed_id}) ===')

    # Clean previous
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
    print(f'OCR: {len(ocr.paginas)} pages')

    print('Detect + IA...')
    res = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
    print(f'Result: encontrado={res.encontrado}, trechos={len(res.trechos)}, pubs={len(res.publicacoes)}')

    print('Persist...')
    database.insert_mencoes(download.edicao_id, res.mencoes_db)
    database.insert_publicacoes(download.edicao_id, res.publicacoes)
    database.salvar_arquivos_atos_locais(ocr.texto_path, res.publicacoes)
    database.update_ocr(download.edicao_id, ocr.texto_path, res.encontrado)

print('\n=== MAY 2026 REPROCESSING COMPLETE ===')
