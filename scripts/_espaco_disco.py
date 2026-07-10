# -*- coding: utf-8 -*-
"""Mostra uso de disco de edicoes/, logs/, atos/ e banco."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import SETTINGS

ROOT = Path(__file__).resolve().parents[1]


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def main() -> int:
    alvos = [
        ("edicoes/", ROOT / "edicoes"),
        ("atos/", ROOT / "atos"),
        ("logs/", ROOT / "logs"),
        ("alertas/", ROOT / "alertas"),
        ("exportacoes/", ROOT / "exportacoes"),
        ("banco", Path(SETTINGS.db_path)),
    ]
    print("\n  === Espaço em disco ===\n")
    total = 0
    for nome, p in alvos:
        s = _size(p)
        total += s
        existe = "OK" if p.exists() else "—"
        extra = ""
        if p.is_dir() and p.exists():
            try:
                n_pdf = len(list(p.rglob("*.pdf")))
                n_ocr = len(list(p.rglob("*.ocr.json")))
                if n_pdf or n_ocr:
                    extra = f"  (pdf={n_pdf} ocr.json={n_ocr})"
            except OSError:
                pass
        print(f"  {nome:16} {_fmt(s):>12}  [{existe}]{extra}")
    print(f"  {'TOTAL':16} {_fmt(total):>12}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
