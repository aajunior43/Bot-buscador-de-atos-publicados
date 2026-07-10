# -*- coding: utf-8 -*-
"""Qualidade / revisão: FN, só-menção, quarentena, auditoria, relatório de pubs.

Uso:
  python scripts/_qualidade.py
  python scripts/_qualidade.py --modo fn
  python scripts/_qualidade.py --modo so-mencao --limite 30
  python scripts/_qualidade.py --modo quarentena
  python scripts/_qualidade.py --modo auditoria
  python scripts/_qualidade.py --modo relatorio --mes 2026-07
  python scripts/_qualidade.py --modo anomalias
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()


def _print_rows(titulo: str, rows: list, campos: list[str]) -> None:
    print(f"\n  === {titulo} ({len(rows)}) ===\n")
    if not rows:
        print("  (vazio)\n")
        return
    for i, r in enumerate(rows, 1):
        if hasattr(r, "keys"):
            d = dict(r)
        else:
            d = r
        parts = []
        for c in campos:
            v = d.get(c, "")
            if v is None:
                v = ""
            s = str(v).replace("\n", " ")
            if len(s) > 60:
                s = s[:57] + "…"
            parts.append(f"{c}={s}")
        print(f"  {i:2}. id={d.get('id','?')}  " + "  ".join(parts))
    print()


def relatorio_pubs(mes: str = "") -> None:
    sql = """
        SELECT p.id, p.tipo, p.numero, p.valor, p.resumo_ia, p.orgao,
               e.data_publicacao, p.importancia
        FROM publicacoes p
        JOIN edicoes e ON e.id = p.edicao_id
        WHERE 1=1
    """
    params: list = []
    if mes:
        sql += " AND e.data_publicacao LIKE ?"
        params.append(f"{mes}%")
    sql += " ORDER BY e.data_publicacao DESC, p.id DESC LIMIT 500"
    with database.connect() as c:
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
        total = len(rows)
        sem_num = sum(1 for r in rows if not (r.get("numero") or "").strip())
        sem_val = sum(1 for r in rows if not (r.get("valor") or "").strip())
        sem_res = sum(1 for r in rows if not (r.get("resumo_ia") or "").strip())
        sem_org = sum(1 for r in rows if not (r.get("orgao") or "").strip())

    filtro = f" mes={mes}" if mes else ""
    print(f"\n  === Relatorio qualidade publicacoes{filtro} ===\n")
    print(f"  Total listado:     {total}")
    print(f"  Sem numero:        {sem_num}")
    print(f"  Sem valor:         {sem_val}")
    print(f"  Sem resumo_ia:     {sem_res}")
    print(f"  Sem orgao:         {sem_org}")
    ruins = [
        r
        for r in rows
        if not (r.get("numero") or "").strip()
        or not (r.get("resumo_ia") or "").strip()
    ]
    if ruins:
        print(f"\n  Pubs com campos fracos ({min(15, len(ruins))} de {len(ruins)}):")
        for r in ruins[:15]:
            print(
                f"    [{r['data_publicacao']}] id={r['id']} {r.get('tipo') or '?'} "
                f"{r.get('numero') or '—'}  "
                f"val={'sim' if r.get('valor') else 'nao'} "
                f"ia={'sim' if r.get('resumo_ia') else 'nao'}"
            )
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--modo",
        default="tudo",
        choices=[
            "tudo",
            "fn",
            "so-mencao",
            "quarentena",
            "auditoria",
            "relatorio",
            "anomalias",
            "lrf",
        ],
    )
    ap.add_argument("--limite", type=int, default=25)
    ap.add_argument("--mes", default="")
    args = ap.parse_args()
    lim = max(1, min(200, args.limite))
    modo = args.modo

    if modo in ("tudo", "fn"):
        rows = database.listar_edicoes_fn_pendente(limit=lim)
        _print_rows(
            "Falsos negativos suspeitos (Inaja, sem pubs, sem fn_sugestao)",
            rows,
            ["data_publicacao", "titulo", "n_mencoes"],
        )

    if modo in ("tudo", "so-mencao"):
        rows = database.listar_edicoes_so_mencao(limit=lim)
        _print_rows(
            "So-mencao (Inaja sem publicacao completa)",
            rows,
            ["data_publicacao", "titulo", "mencoes_count", "termos", "revisao_so_mencao"],
        )

    if modo in ("tudo", "quarentena"):
        rows = database.listar_quarentena(limit=lim)
        _print_rows(
            "Quarentena (muitas falhas de processamento)",
            rows,
            ["data_publicacao", "titulo", "falhas_processamento", "ultima_falha_msg"],
        )

    if modo in ("tudo", "auditoria"):
        rows = database.listar_edicoes_auditoria_pendente(limit=lim)
        _print_rows(
            "Auditoria pendente (Inaja sem pubs, sem auditoria IA)",
            rows,
            ["data_publicacao", "titulo", "n_mencoes", "auditoria_so_mencao"],
        )

    if modo in ("tudo", "anomalias"):
        try:
            rows = database.listar_anomalias(limit=lim)
            _print_rows("Anomalias", rows, list(rows[0].keys())[:6] if rows else ["id"])
        except Exception as exc:
            print(f"\n  Anomalias: indisponivel ({exc})\n")

    if modo in ("tudo", "lrf"):
        try:
            rows = database.listar_radar_lrf(limit=lim)
            _print_rows("Radar LRF", rows, list(rows[0].keys())[:6] if rows else ["id"])
        except Exception as exc:
            print(f"\n  Radar LRF: indisponivel ({exc})\n")

    if modo in ("tudo", "relatorio"):
        relatorio_pubs(args.mes.strip())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
