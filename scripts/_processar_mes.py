# -*- coding: utf-8 -*-
"""Processa edições de um mês (AAAA-MM) via cache OCR + IA, ou OCR real.

Uso:
  python scripts/_processar_mes.py 2026-07
  python scripts/_processar_mes.py 2026-06 --limite 5
  python scripts/_processar_mes.py 2026-06 --ocr-real
  python scripts/_processar_mes.py --dias 30
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import database
from pipeline import processar_edicao_por_id, reprocessar_deteccao_de_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("processar_mes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mes", nargs="?", default="", help="AAAA-MM, ex.: 2026-07")
    ap.add_argument("--limite", type=int, default=0, help="0 = todas")
    ap.add_argument("--notificar", action="store_true")
    ap.add_argument("--sem-ia", action="store_true")
    ap.add_argument(
        "--ocr-real",
        action="store_true",
        help="OCR completo (não só cache .ocr.json)",
    )
    ap.add_argument(
        "--dias",
        type=int,
        default=0,
        help="Em vez de mes: processar últimos N dias",
    )
    ap.add_argument(
        "--mes-atual",
        action="store_true",
        help="Atalho para o mês civil atual",
    )
    args = ap.parse_args()

    if args.sem_ia:
        object.__setattr__(config.SETTINGS, "ai_refine_publications", False)
    else:
        object.__setattr__(config.SETTINGS, "ai_max_calls_por_ciclo", 0)

    database.init_db()

    if args.dias and args.dias > 0:
        desde = (date.today() - timedelta(days=args.dias)).isoformat()
        label = f"ultimos {args.dias} dias (desde {desde})"
        with database.connect() as c:
            rows = c.execute(
                """
                SELECT id, titulo, data_publicacao, caminho_local
                FROM edicoes
                WHERE data_publicacao >= ?
                ORDER BY data_publicacao ASC, id ASC
                """,
                (desde,),
            ).fetchall()
    else:
        mes = args.mes.strip()
        if args.mes_atual or not mes:
            mes = date.today().strftime("%Y-%m")
        if len(mes) != 7 or mes[4] != "-":
            print("Formato inválido. Use AAAA-MM (ex.: 2026-07) ou --dias N")
            return 2
        label = mes
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

    modo = "OCR real" if args.ocr_real else "cache OCR"
    log.info("%s: %s edição(ões) · modo=%s", label, len(rows), modo)
    if not rows:
        print(f"Nenhuma edição em {label}.")
        return 0

    ok = 0
    for i, row in enumerate(rows, 1):
        eid = int(row["id"])
        log.info(
            ">>> [%s/%s] id=%s %s %s",
            i,
            len(rows),
            eid,
            row["data_publicacao"],
            row["titulo"],
        )
        try:
            from ai_processor import reset_ai_call_counter

            reset_ai_call_counter()
        except Exception:
            pass
        try:
            if args.ocr_real:
                r = processar_edicao_por_id(
                    eid,
                    force_ocr=True,
                    fast_ocr=True,
                    notificar_se_encontrado=bool(args.notificar),
                )
            else:
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

    print(f"\n===== RELATÓRIO {label} =====")
    print(f"Processadas com sucesso: {ok}/{len(rows)} ({modo})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
