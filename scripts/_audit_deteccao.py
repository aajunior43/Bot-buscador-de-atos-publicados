# -*- coding: utf-8 -*-
"""Audita edições: hits de Inajá no TXT vs publicações no DB."""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database

database.init_db()


def sem(t: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", t or "")
        if not unicodedata.combining(c)
    ).casefold()


def main() -> None:
    with database.connect() as c:
        rows = c.execute(
            """
            SELECT e.id, e.titulo, e.data_publicacao, e.caminho_local, e.tem_inaja,
                   e.ocr_processado, e.texto_extraido_path,
                   (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id=e.id) n_pub,
                   (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id=e.id) n_men
            FROM edicoes e
            WHERE e.ocr_processado=1 AND e.data_publicacao >= '2025-01-01'
            ORDER BY e.data_publicacao DESC
            """
        ).fetchall()

    print(
        f"{'id':>6} {'data':10} {'pubs':>4} {'men':>4} {'hits':>4} {'hdr':>3} "
        f"{'ocrj':>4} gap"
    )
    under = []
    for r in rows:
        d = dict(r)
        path = d.get("caminho_local") or d.get("texto_extraido_path") or ""
        hits = 0
        headers = 0
        ocrj = False
        if path:
            p = Path(path)
            txt = p.with_suffix(".txt") if p.suffix.lower() == ".pdf" else p
            if not txt.exists() and d.get("texto_extraido_path"):
                txt = Path(d["texto_extraido_path"])
            ocrj = p.with_suffix(".ocr.json").exists() if p.suffix.lower() == ".pdf" else False
            if txt.exists():
                t = txt.read_text(encoding="utf-8", errors="ignore")
                n = sem(t)
                hits = len(re.findall(r"inaja|inava", n))
                headers = len(
                    re.findall(
                        r"prefeitura municipal de ina|municipio de ina|"
                        r"camara municipal de ina|prefeitura de ina",
                        n,
                    )
                )
        gap = ""
        if hits >= 3 and int(d["n_pub"] or 0) <= 1:
            gap = "UNDER"
            under.append(d)
        elif headers >= 2 and int(d["n_pub"] or 0) < headers // 2:
            gap = "LOW"
            under.append(d)
        print(
            f"{d['id']:>6} {d['data_publicacao'] or '-':10} "
            f"{d['n_pub']:>4} {d['n_men']:>4} {hits:>4} {headers:>3} "
            f"{'yes' if ocrj else 'no':>4} {gap}"
        )

    print(f"\nCandidatas a reprocessar: {len(under)}")
    for d in under[:25]:
        print(f"  {d['id']} {d['data_publicacao']} pubs={d['n_pub']} path={d['caminho_local']}")


if __name__ == "__main__":
    main()
