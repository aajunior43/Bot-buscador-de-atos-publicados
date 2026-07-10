# -*- coding: utf-8 -*-
"""Processa as N edições pendentes mais recentes (OCR + detecção + IA).

Uso:
  python scripts/_processar_pendentes.py
  python scripts/_processar_pendentes.py --limite 5
  python scripts/_processar_pendentes.py --limite 3 --sem-ia
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import database
from pipeline import processar_pendentes_automatico

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("processar_pendentes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=5, help="Quantas edições (padrão 5)")
    ap.add_argument("--sem-ia", action="store_true")
    ap.add_argument(
        "--dias",
        type=int,
        default=0,
        help="Só edições dos últimos N dias (0 = usa config)",
    )
    args = ap.parse_args()
    lim = max(1, min(50, args.limite))

    if args.sem_ia:
        object.__setattr__(config.SETTINGS, "ai_refine_publications", False)
    else:
        object.__setattr__(config.SETTINGS, "ai_max_calls_por_ciclo", 0)

    database.init_db()
    with database.connect() as c:
        pend = c.execute(
            "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 0"
        ).fetchone()[0]
    print(f"\n  Pendentes no banco: {pend}")
    print(f"  Processando até {lim}…\n")

    try:
        from ai_processor import reset_ai_call_counter

        reset_ai_call_counter()
    except Exception:
        pass

    n = processar_pendentes_automatico(
        limit=lim,
        max_total=lim,
        lotes=False,
        force_ocr=True,
        fast_ocr=True,
        recent_days=args.dias if args.dias > 0 else None,
        quiet=False,
    )
    print(f"\n  Concluído: {n} edição(ões) processada(s).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
