"""Reprocessa detecção a partir do cache OCR (.ocr.json).

Exemplos:
  python scripts/reprocessar_cache_ocr.py --falsos-negativos
  python scripts/reprocessar_cache_ocr.py --ids 35508,35517
  python scripts/reprocessar_cache_ocr.py --todas-com-cache
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import SETTINGS  # noqa: E402
from pipeline import reprocessar_deteccao_de_cache  # noqa: E402
import database  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("reprocessar_cache")

# IDs priorizados na auditoria (FN / FN provável)
FALSOS_NEGATIVOS_IDS = [
    35508,  # 2026-05-28
    35517,  # 2026-05-12
    35623,  # 2026-02-24
    35526,  # 2026-04-19
    35514,  # 2026-05-17
    33446,  # 2026-06-07
    33444,  # 2026-06-11
]


def _ids_todas_com_cache() -> list[int]:
    conn = sqlite3.connect(SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, caminho_local FROM edicoes
        WHERE caminho_local IS NOT NULL AND caminho_local != ''
          AND ocr_processado = 1
        ORDER BY data_publicacao DESC
        """
    ).fetchall()
    conn.close()
    ids: list[int] = []
    for r in rows:
        p = Path(r["caminho_local"])
        if p.with_suffix(".ocr.json").exists():
            ids.append(int(r["id"]))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Redetectar via cache OCR")
    parser.add_argument("--ids", type=str, help="IDs separados por vírgula")
    parser.add_argument(
        "--falsos-negativos",
        action="store_true",
        help="Reprocessa IDs classificados como FN na auditoria",
    )
    parser.add_argument(
        "--todas-com-cache",
        action="store_true",
        help="Todas as edições com PDF + .ocr.json (pode demorar se IA ligada)",
    )
    parser.add_argument(
        "--notificar",
        action="store_true",
        help="Dispara notificação se encontrar Inajá",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limita quantidade (com --todas-com-cache)",
    )
    args = parser.parse_args()

    database.init_db()
    database.normalizar_tipos_publicacoes_existentes()

    ids: list[int] = []
    if args.ids:
        ids = [int(x.strip()) for x in args.ids.split(",") if x.strip()]
    elif args.falsos_negativos:
        ids = list(FALSOS_NEGATIVOS_IDS)
    elif args.todas_com_cache:
        ids = _ids_todas_com_cache()
        if args.limit > 0:
            ids = ids[: args.limit]
    else:
        parser.error("Use --falsos-negativos, --ids ou --todas-com-cache")

    logger.info("Reprocessando %s edição(ões)...", len(ids))
    ok = 0
    pubs_total = 0
    for eid in ids:
        try:
            resultado = reprocessar_deteccao_de_cache(
                eid, notificar_se_encontrado=args.notificar
            )
            if resultado is None:
                logger.warning("id=%s sem resultado (sem cache/PDF)", eid)
                continue
            n = len(resultado.publicacoes)
            pubs_total += n
            ok += 1
            logger.info(
                "id=%s encontrado=%s pubs=%s mencoes=%s",
                eid,
                resultado.encontrado,
                n,
                len(resultado.mencoes_db),
            )
        except Exception:
            logger.exception("Falha id=%s", eid)

    logger.info("Concluído: %s ok, %s publicações no total (somatório por edição)", ok, pubs_total)


if __name__ == "__main__":
    main()
