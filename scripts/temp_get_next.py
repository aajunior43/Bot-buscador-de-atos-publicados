import sqlite3
from pathlib import Path

conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row
r = conn.execute("SELECT id, titulo, data_publicacao, url, caminho_local FROM edicoes WHERE data_publicacao='2026-06-25' ORDER BY id DESC LIMIT 1").fetchone()
d = dict(r)
print(d)
p = Path(d.get('caminho_local', ''))
print('PDF exists:', p.exists() if p else False)
if p and p.exists():
    print('Size:', p.stat().st_size)
conn.close()