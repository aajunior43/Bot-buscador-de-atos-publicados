"""Backup do SQLite do monitor.

Uso:
  python scripts/backup_db.py
  python scripts/backup_db.py --dir ./backups
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup do jornal_monitor.db")
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Pasta de destino (padrão: logs/backups)",
    )
    args = parser.parse_args()
    database.init_db()
    path = database.backup_database(args.dir)
    print(f"Backup OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
