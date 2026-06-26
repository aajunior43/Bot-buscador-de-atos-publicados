import sqlite3
from pathlib import Path

conn = sqlite3.connect('jornal_monitor.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT id, titulo, caminho_local, texto_extraido_path
    FROM edicoes
    WHERE ocr_processado = 1
""")
rows = cur.fetchall()

removidos_pdf = 0
removidos_txt = 0
espaco_liberado = 0

for r in rows:
    edicao_id = r['id']

    # Apagar PDF
    if r['caminho_local']:
        pdf = Path(r['caminho_local'])
        if pdf.exists():
            size = pdf.stat().st_size
            pdf.unlink()
            espaco_liberado += size
            removidos_pdf += 1
            print(f"[DEL] PDF: {pdf} ({size // 1024 // 1024} MB)")
        else:
            print(f"[SKIP] PDF nao encontrado: {r['caminho_local']}")

    # Apagar TXT
    if r['texto_extraido_path']:
        txt = Path(r['texto_extraido_path'])
        if txt.exists():
            size = txt.stat().st_size
            txt.unlink()
            espaco_liberado += size
            removidos_txt += 1
            print(f"[DEL] TXT: {txt} ({size // 1024} KB)")
        else:
            print(f"[SKIP] TXT nao encontrado: {r['texto_extraido_path']}")

    # Limpar publicacoes e mencoes do banco
    cur.execute("DELETE FROM publicacoes WHERE edicao_id = ?", (edicao_id,))
    cur.execute("DELETE FROM mencoes WHERE edicao_id = ?", (edicao_id,))

    # Resetar campos da edicao
    cur.execute("""
        UPDATE edicoes
        SET ocr_processado = 0,
            caminho_local = NULL,
            hash_md5 = NULL,
            texto_extraido_path = NULL,
            tem_inaja = 0
        WHERE id = ?
    """, (edicao_id,))
    print(f"[DB]  Edicao {edicao_id} ({r['titulo']}) resetada no banco")

conn.commit()
conn.close()

print()
print(f"Concluido!")
print(f"  PDFs removidos : {removidos_pdf}")
print(f"  TXTs removidos : {removidos_txt}")
print(f"  Espaco liberado: {espaco_liberado / 1024 / 1024:.1f} MB")
