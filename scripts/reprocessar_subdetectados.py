# -*- coding: utf-8 -*-
"""Reprocessa detecção a partir do .ocr.json em edições com Inajá subdetectado.

Uso:
  python scripts/reprocessar_subdetectados.py
  python scripts/reprocessar_subdetectados.py --limit 20 --desde 2026-01-01
  python scripts/reprocessar_subdetectados.py --sem-ia
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from pipeline import reprocessar_deteccao_de_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("reprocessar_subdetectados")


def sem(t: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", t or "")
        if not unicodedata.combining(c)
    ).casefold()


def listar_candidatas(*, desde: str, limit: int) -> list[dict]:
    database.init_db()
    with database.connect() as c:
        rows = c.execute(
            """
            SELECT e.id, e.titulo, e.data_publicacao, e.caminho_local,
                   (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id=e.id) n_pub,
                   (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id=e.id) n_men
            FROM edicoes e
            WHERE e.ocr_processado=1
              AND e.data_publicacao >= ?
            ORDER BY e.data_publicacao DESC
            """,
            (desde,),
        ).fetchall()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        path = d.get("caminho_local") or ""
        hits = 0
        headers = 0
        if path:
            p = Path(path)
            txt = p.with_suffix(".txt")
            ocrj = p.with_suffix(".ocr.json")
            if not ocrj.exists():
                continue
            if txt.exists():
                t = txt.read_text(encoding="utf-8", errors="ignore")
                n = sem(t)
                hits = len(re.findall(r"inaja|inava", n))
                headers = len(
                    re.findall(
                        r"prefeitura municipal de ina|municipio de ina|"
                        r"camara municipal de ina",
                        n,
                    )
                )
        n_pub = int(d["n_pub"] or 0)
        # Subdetectado: muitos hits/headers e poucas pubs, ou só-menção com hits
        if hits >= 3 and n_pub <= max(1, hits // 4):
            d["hits"] = hits
            d["headers"] = headers
            out.append(d)
        elif headers >= 2 and n_pub < max(1, headers // 2):
            d["hits"] = hits
            d["headers"] = headers
            out.append(d)
        elif hits >= 2 and n_pub == 0:
            d["hits"] = hits
            d["headers"] = headers
            out.append(d)
        if len(out) >= limit:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2025-01-01")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument(
        "--sem-ia",
        action="store_true",
        help="Desliga refine IA (só detector heurístico)",
    )
    ap.add_argument("--notificar", action="store_true")
    args = ap.parse_args()

    if args.sem_ia:
        import config

        object.__setattr__(config.SETTINGS, "ai_refine_publications", False)
        logger.info("IA desligada para este lote")
    else:
        import config

        # Lote de reprocessamento: não cortar no meio (0 = sem teto)
        object.__setattr__(config.SETTINGS, "ai_max_calls_por_ciclo", 0)
        logger.info("AI_MAX_CALLS_POR_CICLO desligado para o lote")

    cands = listar_candidatas(desde=args.desde, limit=args.limit)
    logger.info("Candidatas: %s", len(cands))
    ok = 0
    for d in cands:
        eid = int(d["id"])
        antes = int(d["n_pub"] or 0)
        logger.info(
            ">>> id=%s %s pubs=%s hits=%s headers=%s",
            eid,
            d.get("data_publicacao"),
            antes,
            d.get("hits"),
            d.get("headers"),
        )
        try:
            try:
                from ai_processor import reset_ai_call_counter

                reset_ai_call_counter()
            except Exception:
                pass
            r = reprocessar_deteccao_de_cache(
                eid, notificar_se_encontrado=bool(args.notificar)
            )
            if r is None:
                logger.warning("id=%s sem resultado (cache/PDF)", eid)
                continue
            depois = len(r.publicacoes)
            logger.info(
                "<<< id=%s pubs %s → %s men=%s",
                eid,
                antes,
                depois,
                len(r.mencoes_db),
            )
            ok += 1
        except Exception:
            logger.exception("falha id=%s", eid)
    logger.info("Concluído: %s/%s edições reprocessadas", ok, len(cands))


if __name__ == "__main__":
    main()
