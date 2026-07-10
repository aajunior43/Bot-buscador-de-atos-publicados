# -*- coding: utf-8 -*-
"""Processa edições de um mês (AAAA-MM) via cache OCR + IA.

Uso:
  python scripts/_processar_mes.py 2026-07
  python scripts/_processar_mes.py 2026-06 --limite 5
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import database
from pipeline import reprocessar_deteccao_de_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("processar_mes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mes", help="AAAA-MM, ex.: 2026-07")
    ap.add_argument("--limite", type=int, default=0, help="0 = todas do mês")
    ap.add_argument("--notificar", action="store_true")
    ap.add_argument("--sem-ia", action="store_true")
    args = ap.parse_args()
    mes = args.mes.strip()
    if len(mes) != 7 or mes[4] != "-":
        print("Formato inválido. Use AAAA-MM (ex.: 2026-07)")
        return 2

    if args.sem_ia:
        object.__setattr__(config.SETTINGS, "ai_refine_publications", False)
    else:
        object.__setattr__(config.SETTINGS, "ai_max_calls_por_ciclo", 0)

    database.init_db()
    with database.connect() as c:
        rows = c.execute(
            """
            SELECT id, titulo, data_publicacao, caminho_local
            FROM edicoes
            WHERE data_publicacao LIKE ?
            ORDER BY data_publicacao ASC, id ASC
            """,
            (f"{mes}%",),
        ).fetchall()

    if args.limite and args.limite > 0:
        rows = rows[: args.limite]

    log.info("Mês %s: %s edição(ões)", mes, len(rows))
    if not rows:
        print(f"Nenhuma edição em {mes}.")
        return 0

    ok = 0
    for row in rows:
        eid = int(row["id"])
        log.info(">>> id=%s %s %s", eid, row["data_publicacao"], row["titulo"])
        try:
            from ai_processor import reset_ai_call_counter

            reset_ai_call_counter()
        except Exception:
            pass
        try:
            r = reprocessar_deteccao_de_cache(
                eid, notificar_se_encontrado=bool(args.notificar)
            )
            if r is None:
                log.warning("id=%s sem resultado", eid)
                continue
            log.info(
                "<<< id=%s inaja=%s pubs=%s men=%s",
                eid,
                r.encontrado,
                len(r.publicacoes),
                len(r.mencoes_db),
            )
            for p in r.publicacoes:
                log.info(
                    "    · %s %s | %s | %s",
                    p.get("tipo") or "?",
                    p.get("numero") or "",
                    (p.get("orgao") or "—")[:36],
                    (p.get("resumo_ia") or p.get("assunto") or "")[:70],
                )
            ok += 1
        except Exception:
            log.exception("falha id=%s", eid)

    print(f"\n===== RELATÓRIO {mes} =====")
    with database.connect() as c:
        eds = c.execute(
            """
            SELECT e.id, e.data_publicacao, e.ocr_processado, e.tem_inaja,
                   (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id=e.id) n_pub,
                   (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id=e.id) n_men
            FROM edicoes e
            WHERE e.data_publicacao LIKE ?
            ORDER BY e.data_publicacao
            """,
            (f"{mes}%",),
        ).fetchall()
        for e in eds:
            print(
                f"{e['data_publicacao']} id={e['id']} ocr={e['ocr_processado']} "
                f"inaja={e['tem_inaja']} pubs={e['n_pub']} men={e['n_men']}"
            )
    print(f"\nProcessadas com sucesso nesta rodada: {ok}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
