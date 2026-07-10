# -*- coding: utf-8 -*-
"""Status resumido da fila / banco."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database

database.init_db()
with database.connect() as conn:
    tot = conn.execute("SELECT COUNT(*) FROM edicoes").fetchone()[0]
    pend = conn.execute(
        "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 0"
    ).fetchone()[0]
    ok = conn.execute(
        "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 1"
    ).fetchone()[0]
    inaja = conn.execute(
        "SELECT COUNT(*) FROM edicoes WHERE tem_inaja = 1"
    ).fetchone()[0]
    pubs = conn.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
    men = conn.execute("SELECT COUNT(*) FROM mencoes").fetchone()[0]

print()
print(f"  Edicoes:      {tot}")
print(f"  Processadas:  {ok}")
print(f"  Pendentes:    {pend}")
print(f"  Com Inaja:    {inaja}")
print(f"  Publicacoes:  {pubs}")
print(f"  Mencoes:      {men}")
print()
