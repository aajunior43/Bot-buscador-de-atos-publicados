# -*- coding: utf-8 -*-
"""Invalida cache OCR (.ocr.json / .txt) de um mês ou de um ID.

Uso:
  python scripts/_invalidar_ocr.py --mes 2026-06
  python scripts/_invalidar_ocr.py --id 67935
  python scripts/_invalidar_ocr.py --mes 2026-06 --dry-run
  python scripts/_invalidar_ocr.py --mes 2026-06 --zerar-flag
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--mes", help="AAAA-MM")
    g.add_argument("--id", type=int, dest="edicao_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--zerar-flag",
        action="store_true",
        help="Também marca ocr_processado=0 no banco",
    )
    args = ap.parse_args()

    with database.connect() as c:
        if args.edicao_id:
            rows = c.execute(
                "SELECT id, data_publicacao, caminho_local, ocr_processado "
                "FROM edicoes WHERE id=?",
                (args.edicao_id,),
            ).fetchall()
        else:
            mes = args.mes.strip()
            if len(mes) != 7 or mes[4] != "-":
                print("Mes inválido AAAA-MM")
                return 2
            rows = c.execute(
                "SELECT id, data_publicacao, caminho_local, ocr_processado "
                "FROM edicoes WHERE data_publicacao LIKE ? "
                "ORDER BY data_publicacao",
                (f"{mes}%",),
            ).fetchall()

    if not rows:
        print("Nenhuma edição.")
        return 0

    removidos = 0
    for r in rows:
        caminho = r["caminho_local"] or ""
        if not caminho:
            print(f"  id={r['id']} sem caminho_local")
            continue
        p = Path(caminho)
        for suf in (".ocr.json", ".txt"):
            f = p.with_suffix(suf) if suf != ".ocr.json" else Path(str(p) + ".ocr.json")
            # pdf path: file.pdf -> file.ocr.json via with_suffix('.ocr.json') is wrong
            # correct: Path(str(p.with_suffix('')) + '.ocr.json') or p.with_name(p.stem + '.ocr.json')
        candidates = {
            p.with_name(p.stem + ".ocr.json"),
            p.with_suffix(".txt"),
        }
        # Alguns caches usam nome.pdf.ocr.json
        candidates.add(Path(str(p) + ".ocr.json"))
        seen: set[str] = set()
        for f in candidates:
            try:
                key = str(f.resolve()) if f.exists() else str(f)
            except OSError:
                key = str(f)
            if key in seen:
                continue
            seen.add(key)
            if f.exists():
                print(f"  {'[dry] ' if args.dry_run else ''}remover {f}")
                if not args.dry_run:
                    try:
                        f.unlink()
                        removidos += 1
                    except OSError as exc:
                        print(f"    falha: {exc}")


    if args.zerar_flag and not args.dry_run:
        ids = [int(r["id"]) for r in rows]
        with database.connect() as c:
            for eid in ids:
                c.execute(
                    "UPDATE edicoes SET ocr_processado=0, tem_inaja=0 WHERE id=?",
                    (eid,),
                )
        print(f"  Flags OCR zeradas: {len(ids)} edição(ões)")

    print(f"\n  Edições: {len(rows)}  arquivos removidos: {removidos}")
    print("  (dry-run)" if args.dry_run else "  OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
