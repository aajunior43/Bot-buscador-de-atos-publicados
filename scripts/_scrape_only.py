# -*- coding: utf-8 -*-
"""Só varre o site e cadastra edições novas (sem OCR).

Uso:
  python scripts/_scrape_only.py
  python scripts/_scrape_only.py --baixar
  python scripts/_scrape_only.py --force-rescan
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from scraper import listar_edicoes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("scrape_only")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-rescan", action="store_true")
    ap.add_argument(
        "--baixar",
        action="store_true",
        help="Também baixa PDFs (ainda sem OCR)",
    )
    args = ap.parse_args()

    database.init_db()
    novas = listar_edicoes(force_rescan=bool(args.force_rescan))
    print(f"\n  Edições novas (após limite do ciclo): {len(novas)}\n")
    ok = 0
    for e in novas:
        print(f"  · {e.data_publicacao}  {e.titulo}")
        print(f"    {e.url}")
        try:
            eid = database.insert_or_get_edicao(
                e.url, e.titulo or "", e.data_publicacao
            )
            print(f"    id={eid}")
            if args.baixar:
                from downloader import baixar_edicao

                path = baixar_edicao(e)
                print(f"    PDF: {path}")
            ok += 1
        except Exception as exc:
            log.warning("falha: %s", exc)
    print(f"\n  Cadastradas/atualizadas: {ok}/{len(novas)}")
    print("  OCR não rodou — use processar pendentes ou o BOT.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
