# -*- coding: utf-8 -*-
"""Processa uma edição por ID (OCR real ou só cache).

Uso:
  python scripts/_processar_id.py 67935
  python scripts/_processar_id.py 67935 --cache
  python scripts/_processar_id.py 67935 --force-ocr
  python scripts/_processar_id.py 67935 --notificar
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import database
from pipeline import processar_edicao_por_id, reprocessar_deteccao_de_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("processar_id")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edicao_id", type=int)
    ap.add_argument("--cache", action="store_true", help="Só detecção do .ocr.json")
    ap.add_argument("--force-ocr", action="store_true")
    ap.add_argument("--notificar", action="store_true")
    ap.add_argument("--sem-ia", action="store_true")
    args = ap.parse_args()

    if args.sem_ia:
        object.__setattr__(config.SETTINGS, "ai_refine_publications", False)
    else:
        object.__setattr__(config.SETTINGS, "ai_max_calls_por_ciclo", 0)

    database.init_db()
    eid = int(args.edicao_id)
    with database.connect() as c:
        row = c.execute(
            "SELECT id, titulo, data_publicacao, ocr_processado FROM edicoes WHERE id=?",
            (eid,),
        ).fetchone()
    if not row:
        print(f"Edição id={eid} não encontrada.")
        return 2

    print(
        f"\n  id={row['id']}  {row['data_publicacao']}  "
        f"ocr={row['ocr_processado']}  {row['titulo']}\n"
    )
    try:
        from ai_processor import reset_ai_call_counter

        reset_ai_call_counter()
    except Exception:
        pass

    try:
        if args.cache:
            r = reprocessar_deteccao_de_cache(
                eid, notificar_se_encontrado=bool(args.notificar)
            )
        else:
            r = processar_edicao_por_id(
                eid,
                force_ocr=True,
                fast_ocr=not bool(args.force_ocr),
                notificar_se_encontrado=bool(args.notificar),
            )
    except Exception:
        log.exception("falha id=%s", eid)
        return 1

    if r is None:
        print("  Sem resultado (PDF/cache ausente ou lock).")
        return 1
    print(
        f"  OK inaja={r.encontrado} pubs={len(r.publicacoes)} "
        f"men={len(r.mencoes_db)}"
    )
    for p in r.publicacoes:
        print(
            f"    · {p.get('tipo') or '?'} {p.get('numero') or ''} | "
            f"{(p.get('orgao') or '—')[:40]} | "
            f"{(p.get('resumo_ia') or p.get('assunto') or '')[:70]}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
