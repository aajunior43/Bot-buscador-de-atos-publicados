import sqlite3
from pathlib import Path
import json

conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row

# Contar arquivos
ocr_files = list(Path('edicoes').rglob('*.ocr.json'))
atos_files = list(Path('edicoes').rglob('*.atos.json'))
print(f'Arquivos .ocr.json no disco: {len(ocr_files)}')
print(f'Arquivos .atos.json no disco: {len(atos_files)}')

# Verificar se nomes batem
ocr_bases = {f.stem.replace('.ocr', '') for f in ocr_files}
atos_bases = {f.stem.replace('.atos', '') for f in atos_files}
print(f'Edições únicas com .ocr.json: {len(ocr_bases)}')
print(f'Edições únicas com .atos.json: {len(atos_bases)}')
print(f'Nomes batem? {ocr_bases == atos_bases}')

# Edições no DB com ocr_processado=1
db_ocr = conn.execute('SELECT COUNT(1) FROM edicoes WHERE ocr_processado=1').fetchone()[0]
print(f'Edições com ocr_processado=1 no DB: {db_ocr}')

# Edições que têm texto_extraido_path mas ocr_processado=0
mismatch = conn.execute("SELECT COUNT(1) FROM edicoes WHERE texto_extraido_path IS NOT NULL AND texto_extraido_path != '' AND ocr_processado=0").fetchone()[0]
print(f'Edições com texto mas flag ocr_processado=0: {mismatch}')

# Verificar algumas .ocr.json recentes se têm avisos
print()
print('=== Amostra de .ocr.json (avisos e páginas) ===')
recent_ocr = sorted(ocr_files, key=lambda x: x.stat().st_mtime, reverse=True)[:5]
for f in recent_ocr:
    try:
        data = json.loads(f.read_text(encoding='utf-8', errors='ignore'))
        avisos = data.get('avisos', [])
        paginas = len(data.get('paginas', []))
        print(f"{f.name}: {paginas} páginas, avisos: {len(avisos)} | {avisos[:2] if avisos else 'nenhum'}")
    except Exception as e:
        print(f'{f.name}: erro ao ler - {e}')

conn.close()