# -*- coding: utf-8 -*-
"""Diagnóstico completo do ambiente (PATH, IA, banco, lock, disco)."""
from __future__ import annotations

import os
import shutil
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SETTINGS
import database
from process_lock import DEFAULT_LOCK

ROOT = Path(__file__).resolve().parents[1]


def _ok(label: str, ok: bool, detail: str = "") -> None:
    mark = "OK " if ok else "FALHA"
    extra = f"  — {detail}" if detail else ""
    print(f"  [{mark}] {label}{extra}")


def _size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_size / (1024 * 1024)
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        return 0.0
    return total / (1024 * 1024)


def main() -> int:
    print("\n  === DIAGNOSTICO DO SISTEMA ===\n")
    print("  --- Ambiente ---")
    _ok("Python", True, sys.version.split()[0])
    _ok("Projeto", ROOT.is_dir(), str(ROOT))

    tess = shutil.which("tesseract") or (
        SETTINGS.tesseract_path if getattr(SETTINGS, "tesseract_path", "") else ""
    )
    if not tess and os.getenv("TESSERACT_PATH"):
        tess = os.getenv("TESSERACT_PATH", "")
    # config may use tesseract_cmd style
    for cand in (
        getattr(SETTINGS, "tesseract_path", "") or "",
        os.getenv("TESSERACT_PATH", ""),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    ):
        if cand and Path(cand).exists():
            tess = cand
            break
    _ok("Tesseract", bool(tess and (shutil.which("tesseract") or Path(str(tess)).exists())), str(tess or "nao encontrado"))

    poppler = os.getenv("POPPLER_PATH", "") or getattr(SETTINGS, "poppler_path", "") or ""
    poppler_ok = False
    for cand in (
        poppler,
        r"C:\Poppler\poppler-24.02.0\Library\bin",
        r"C:\poppler\Library\bin",
    ):
        if cand and Path(cand).exists():
            poppler = cand
            poppler_ok = True
            break
    if not poppler_ok and shutil.which("pdftoppm"):
        poppler_ok = True
        poppler = shutil.which("pdftoppm") or "pdftoppm"
    _ok("Poppler (pdf2image)", poppler_ok, str(poppler or "nao encontrado"))

    print("\n  --- Config / canais ---")
    try:
        from ai_processor import ia_disponivel, _api_key

        key = _api_key()
        _ok("IA disponivel", ia_disponivel(), SETTINGS.opencode_model)
        _ok("Chave IA", bool(key), ("…" + key[-6:]) if key and len(key) > 6 else "vazia")
    except Exception as exc:
        _ok("IA", False, str(exc))
    _ok("AI_REFINE_PUBLICATIONS", bool(SETTINGS.ai_refine_publications))


    alert_dir = getattr(SETTINGS, "alert_dir", None)
    _ok("Pasta alertas/", bool(alert_dir), str(alert_dir or ""))
    _ok(
        "Web auth",
        bool(os.getenv("WEBAPP_USER") and os.getenv("WEBAPP_PASSWORD")),
        "ativo" if os.getenv("WEBAPP_USER") else "desligado",
    )

    print("\n  --- Banco / fila ---")
    try:
        database.init_db()
        with database.connect() as c:
            tot = c.execute("SELECT COUNT(*) FROM edicoes").fetchone()[0]
            pend = c.execute(
                "SELECT COUNT(*) FROM edicoes WHERE ocr_processado=0"
            ).fetchone()[0]
            pubs = c.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
            jobs = c.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='rodando'"
            ).fetchone()[0]
        _ok("SQLite", Path(SETTINGS.db_path).exists(), f"{SETTINGS.db_path}")
        _ok("Edicoes", True, f"{tot} total · {pend} pendentes · {pubs} pubs")
        _ok("Jobs rodando", jobs == 0, str(jobs))
        st = database.get_status_automacao()
        _ok("BOT vivo", bool(st.get("bot_vivo")), st.get("bot_ultimo") or "sem ciclo")
        _ok("Quarentena", True, str(st.get("quarentena_count", 0)))
    except Exception as exc:
        _ok("Banco", False, str(exc))

    print("\n  --- Lock / disco / rede ---")
    lock_on = DEFAULT_LOCK.exists()
    _ok("Lock livre", not lock_on, str(DEFAULT_LOCK) if lock_on else "sem lock")
    ed_mb = _size_mb(ROOT / "edicoes")
    _ok("Pasta edicoes/", (ROOT / "edicoes").exists(), f"{ed_mb:.1f} MB")
    _ok("Pasta logs/", SETTINGS.log_dir.exists(), f"{_size_mb(SETTINGS.log_dir):.1f} MB")

    # Web port
    port_ok = False
    try:
        with socket.create_connection(("127.0.0.1", 8001), timeout=0.4):
            port_ok = True
    except OSError:
        port_ok = False
    _ok("Web :8001 respondendo", port_ok, "sim" if port_ok else "nao (normal se web parada)")

    # Site reachability (optional quick)
    try:
        import urllib.request

        req = urllib.request.Request(
            SETTINGS.site_url,
            headers={"User-Agent": SETTINGS.user_agent},
            method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            _ok("Site O Regional", 200 <= resp.status < 400, f"HTTP {resp.status}")
    except Exception as exc:
        _ok("Site O Regional", False, str(exc)[:80])

    print("\n  === fim diagnostico ===\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
