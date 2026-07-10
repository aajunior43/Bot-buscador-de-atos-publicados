# -*- coding: utf-8 -*-
"""Remove lock de processamento se existir."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import SETTINGS
from process_lock import DEFAULT_LOCK

candidatos = [
    DEFAULT_LOCK,
    ROOT / "logs" / "processamento.lock",
    ROOT / "processamento.lock",
    Path(getattr(SETTINGS, "log_dir", ROOT / "logs")) / "processamento.lock",
]
# unique by resolve
seen: set[str] = set()
removidos = 0
for p in candidatos:
    try:
        key = str(p.resolve()) if p.exists() else str(p)
    except OSError:
        key = str(p)
    if key in seen:
        continue
    seen.add(key)
    try:
        if p.exists():
            # Mostra conteúdo (pid:label) se possível
            try:
                info = p.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                info = ""
            p.unlink()
            print(f"  Removido: {p}" + (f"  ({info})" if info else ""))
            removidos += 1
    except Exception as exc:
        print(f"  Falha ao remover {p}: {exc}")

if removidos == 0:
    print("  Nenhum lock encontrado.")
else:
    print(f"  OK ({removidos}).")
