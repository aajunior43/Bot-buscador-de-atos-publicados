# -*- coding: utf-8 -*-
"""Apaga resultados de processamento para reprocessar do zero.

Mantém: edições cadastradas, PDFs baixados, .ocr.json (cache).
Apaga: publicações, menções, jobs, notificações, métricas, flags OCR, pasta atos/.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Só mostra o que seria apagado, sem alterar",
    )
    args = ap.parse_args()

    database.init_db()
    root = Path(__file__).resolve().parents[1]

    with database.connect() as c:
        before = {
            "edicoes": c.execute("SELECT COUNT(*) FROM edicoes").fetchone()[0],
            "publicacoes": c.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0],
            "mencoes": c.execute("SELECT COUNT(*) FROM mencoes").fetchone()[0],
            "jobs": c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "notificacoes": c.execute("SELECT COUNT(*) FROM notificacoes").fetchone()[0],
            "metricas": c.execute(
                "SELECT COUNT(*) FROM deteccao_metricas"
            ).fetchone()[0],
        }
        print("ANTES:", before)
        if args.dry_run:
            print()
            print("DRY-RUN: nada foi apagado.")
            print(
                "Seria removido: publicacoes, mencoes, jobs, notificacoes, "
                "metricas; flags OCR zeradas; pasta atos/ limpa."
            )
            print("Mantido: edicoes, PDFs, .ocr.json")
            return

        c.execute("DELETE FROM publicacoes")
        c.execute("DELETE FROM mencoes")
        c.execute("DELETE FROM deteccao_metricas")
        c.execute("DELETE FROM jobs")
        c.execute("DELETE FROM notificacoes")

        cols = {r[1] for r in c.execute("PRAGMA table_info(edicoes)").fetchall()}
        sets = [
            "ocr_processado = 0",
            "tem_inaja = 0",
            "texto_extraido_path = NULL",
        ]
        extras = {
            "revisao_so_mencao": "revisao_so_mencao = NULL",
            "falhas_processamento": "falhas_processamento = 0",
            "ultima_falha_em": "ultima_falha_em = NULL",
            "ultima_falha_msg": "ultima_falha_msg = NULL",
            "score_candidatura": "score_candidatura = 0",
            "score_prioridade": "score_prioridade = 1",
            "auditoria_so_mencao": "auditoria_so_mencao = NULL",
            "fn_sugestao": "fn_sugestao = NULL",
        }
        for col, sql in extras.items():
            if col in cols:
                sets.append(sql)
        c.execute("UPDATE edicoes SET " + ", ".join(sets))

        c.execute(
            "DELETE FROM settings WHERE chave LIKE 'resumo_diario%' "
            "OR chave LIKE 'triagem_lote_%'"
        )

        after = {
            "edicoes": c.execute("SELECT COUNT(*) FROM edicoes").fetchone()[0],
            "publicacoes": c.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0],
            "mencoes": c.execute("SELECT COUNT(*) FROM mencoes").fetchone()[0],
            "jobs": c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "notificacoes": c.execute(
                "SELECT COUNT(*) FROM notificacoes"
            ).fetchone()[0],
            "metricas": c.execute(
                "SELECT COUNT(*) FROM deteccao_metricas"
            ).fetchone()[0],
            "pendentes_ocr": c.execute(
                "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 0"
            ).fetchone()[0],
        }
        print("DEPOIS:", after)

    # Pasta de atos espelhados
    atos = root / "atos"
    if atos.exists():
        for sub in ("por-data", "por-orgao", "por-tipo"):
            p = atos / sub
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
                p.mkdir(parents=True, exist_ok=True)
                print("limpo:", p.relative_to(root))
        idx = atos / "INDICE.md"
        idx.write_text(
            "# Atos\n\n(reprocessar — pasta limpa)\n", encoding="utf-8"
        )

    # Lock de processamento
    for lock in (
        root / "logs" / "processamento.lock",
        root / "processamento.lock",
    ):
        if lock.exists():
            lock.unlink()
            print("lock removido:", lock.relative_to(root))

    print()
    print("OK. Mantidos: edições cadastradas + PDFs + .ocr.json")
    print("Apagados: publicações, menções, jobs, alertas, métricas, flags OCR, atos/")
    print("Pode iniciar o BOT — ele reprocessa a fila (usa cache OCR quando existir).")


if __name__ == "__main__":
    main()
