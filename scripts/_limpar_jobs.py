# -*- coding: utf-8 -*-
"""Marca jobs travados em 'rodando' como erro (crash anterior).

Uso:
  python scripts/_limpar_jobs.py
  python scripts/_limpar_jobs.py --apagar-erros
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apagar-erros",
        action="store_true",
        help="Também apaga jobs com status=erro",
    )
    args = ap.parse_args()

    with database.connect() as c:
        rodando = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'rodando'"
        ).fetchone()[0]
        erros = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'erro'"
        ).fetchone()[0]
        print(f"\n  Jobs rodando: {rodando}")
        print(f"  Jobs erro:    {erros}")

        if rodando:
            c.execute(
                """
                UPDATE jobs
                SET status = 'erro',
                    mensagem = COALESCE(mensagem, '') || ' [encerrado via menu]',
                    atualizado_em = datetime('now','localtime')
                WHERE status = 'rodando'
                """
            )
            print(f"  → {rodando} job(s) marcados como erro.")
        else:
            print("  Nenhum job rodando.")

        if args.apagar_erros:
            n = c.execute("DELETE FROM jobs WHERE status = 'erro'").rowcount
            print(f"  → {n} job(s) de erro apagados.")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
