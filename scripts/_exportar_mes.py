# -*- coding: utf-8 -*-
"""Exporta publicações de um mês para CSV em ./exportacoes/.

Uso:
  python scripts/_exportar_mes.py 2026-07
  python scripts/_exportar_mes.py 2026-06 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exporter import exportar_csv, exportar_json

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mes", help="AAAA-MM")
    ap.add_argument("--json", action="store_true", help="Também grava JSON")
    args = ap.parse_args()
    mes = args.mes.strip()
    if len(mes) != 7 or mes[4] != "-":
        print("Formato inválido. Use AAAA-MM")
        return 2

    inicio = f"{mes}-01"
    # fim inclusivo aproximado
    y, m = int(mes[:4]), int(mes[5:7])
    if m == 12:
        fim = f"{y}-12-31"
    else:
        fim = f"{y}-{m:02d}-31"  # SQLite compara strings; 31 cobre o mês

    out_dir = ROOT / "exportacoes"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"publicacoes_{mes}_{stamp}.csv"
    csv_path.write_text(
        exportar_csv(data_inicio=inicio, data_fim=fim),
        encoding="utf-8-sig",
    )
    print(f"\n  CSV: {csv_path}")

    if args.json:
        rows = exportar_json(data_inicio=inicio, data_fim=fim)
        jpath = out_dir / f"publicacoes_{mes}_{stamp}.json"
        jpath.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  JSON: {jpath}  ({len(rows)} regs)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
