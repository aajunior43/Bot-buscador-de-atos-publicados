#!/usr/bin/env python3
"""Reconstrói a pasta atos/ a partir do banco SQLite.

Uso:
    python scripts/reconstruir_atos.py
    python scripts/reconstruir_atos.py --dir ./atos
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import atos_arquivo
import database
from config import SETTINGS


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Rebuild pasta atos/ organizada")
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help=f"Destino (padrão: {SETTINGS.atos_dir})",
    )
    parser.add_argument(
        "--sem-limpar",
        action="store_true",
        help="Não apaga por-data/tipo/orgao antes (só sobrescreve)",
    )
    args = parser.parse_args()
    database.init_db()
    dest = args.dir or SETTINGS.atos_dir
    print(f"Reconstruindo atos em: {dest.resolve()}")
    stats = atos_arquivo.reconstruir_tudo_do_banco(
        root=dest, limpar=not args.sem_limpar
    )
    print(f"OK: {stats['atos']} ato(s) de {stats['edicoes']} edição(ões).")
    print(f"Abra: {Path(dest).resolve() / 'INDICE.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
