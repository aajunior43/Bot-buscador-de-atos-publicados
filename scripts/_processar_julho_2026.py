# -*- coding: utf-8 -*-
"""Processa edições de 2026-07 a partir do cache OCR + IA."""
from __future__ import annotations

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
log = logging.getLogger("julho2026")


def main() -> None:
    # Lote de validação: sem teto de calls
    object.__setattr__(config.SETTINGS, "ai_max_calls_por_ciclo", 0)
    database.init_db()

    with database.connect() as c:
        rows = c.execute(
            """
            SELECT id, titulo, data_publicacao, caminho_local
            FROM edicoes
            WHERE data_publicacao LIKE '2026-07%'
            ORDER BY data_publicacao ASC, id ASC
            """
        ).fetchall()

    log.info("Edições julho/2026: %s", len(rows))
    for row in rows:
        eid = int(row["id"])
        log.info(
            ">>> id=%s %s %s",
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
            r = reprocessar_deteccao_de_cache(
                eid, notificar_se_encontrado=False
            )
            if r is None:
                log.warning("id=%s sem resultado (cache/PDF)", eid)
                continue
            log.info(
                "<<< id=%s encontrado=%s pubs=%s men=%s pags=%s",
                eid,
                r.encontrado,
                len(r.publicacoes),
                len(r.mencoes_db),
                r.paginas_com_mencao,
            )
            for p in r.publicacoes:
                log.info(
                    "    · %s %s | %s | %s",
                    p.get("tipo") or "?",
                    p.get("numero") or "",
                    (p.get("orgao") or "—")[:40],
                    (p.get("resumo_ia") or p.get("assunto") or "")[:70],
                )
        except Exception:
            log.exception("falha id=%s", eid)

    # Relatório DB
    print("\n===== RELATÓRIO FINAL JULHO/2026 =====")
    with database.connect() as c:
        eds = c.execute(
            """
            SELECT e.id, e.data_publicacao, e.titulo, e.ocr_processado, e.tem_inaja,
                   (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id=e.id) n_pub,
                   (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id=e.id) n_men
            FROM edicoes e
            WHERE e.data_publicacao LIKE '2026-07%'
            ORDER BY e.data_publicacao
            """
        ).fetchall()
        for e in eds:
            print(
                f"{e['data_publicacao']} id={e['id']} ocr={e['ocr_processado']} "
                f"inaja={e['tem_inaja']} pubs={e['n_pub']} men={e['n_men']}"
            )
            pubs = c.execute(
                """
                SELECT tipo, numero, orgao, valor, importancia, resumo_ia, assunto
                FROM publicacoes WHERE edicao_id=?
                ORDER BY pagina, id
                """,
                (e["id"],),
            ).fetchall()
            for p in pubs:
                print(
                    f"  - {p['tipo'] or '?'} {p['numero'] or ''} | "
                    f"{(p['orgao'] or '—')[:35]} | {p['valor'] or ''} | "
                    f"★{p['importancia'] or '-'} | "
                    f"{(p['resumo_ia'] or p['assunto'] or '')[:80]}"
                )


if __name__ == "__main__":
    main()
