# -*- coding: utf-8 -*-
"""Limpeza de disco: PDFs antigos sem Inajá + retenção de backups.

Uso:
  python scripts/_limpeza_disco.py --dry-run
  python scripts/_limpeza_disco.py --meses 18 --aplicar
  python scripts/_limpeza_disco.py --backups 10 --aplicar
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from config import SETTINGS


def limpar_pdfs_sem_inaja(*, meses: int, dry_run: bool) -> dict:
    """Remove PDFs de edições processadas, sem Inajá, mais velhas que N meses.

    Mantém .ocr.json e registro no banco.
    """
    piso = (date.today() - timedelta(days=max(30, meses) * 30)).isoformat()
    liberados = 0
    bytes_ = 0
    itens: list[str] = []
    database.init_db()
    with database.connect() as c:
        rows = c.execute(
            """
            SELECT id, data_publicacao, caminho_local, tem_inaja, ocr_processado
            FROM edicoes
            WHERE ocr_processado = 1
              AND COALESCE(tem_inaja, 0) = 0
              AND data_publicacao IS NOT NULL
              AND data_publicacao < ?
              AND caminho_local IS NOT NULL AND caminho_local != ''
            ORDER BY data_publicacao ASC
            """,
            (piso,),
        ).fetchall()
    for r in rows:
        p = Path(r["caminho_local"])
        if not p.exists() or p.suffix.lower() != ".pdf":
            continue
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        liberados += 1
        bytes_ += sz
        itens.append(f"{r['data_publicacao']} id={r['id']} {p.name} ({sz // 1024} KB)")
        if not dry_run:
            try:
                p.unlink()
            except OSError as exc:
                itens.append(f"  falha: {exc}")
    return {
        "piso": piso,
        "arquivos": liberados,
        "bytes": bytes_,
        "mb": round(bytes_ / (1024 * 1024), 1),
        "itens": itens[:40],
        "dry_run": dry_run,
    }


def reter_backups(*, manter: int, dry_run: bool) -> dict:
    pasta = Path(SETTINGS.log_dir) / "backups"
    if not pasta.is_dir():
        return {"removidos": 0, "mantidos": 0, "dry_run": dry_run}
    files = sorted(
        pasta.glob("jornal_monitor_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    manter = max(1, manter)
    keep = files[:manter]
    drop = files[manter:]
    for f in drop:
        if not dry_run:
            try:
                f.unlink()
            except OSError:
                pass
    return {
        "mantidos": len(keep),
        "removidos": len(drop),
        "dry_run": dry_run,
        "lista_removidos": [p.name for p in drop[:20]],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meses", type=int, default=18, help="PDFs sem Inajá mais velhos")
    ap.add_argument("--backups", type=int, default=10, help="Quantos backups manter")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--aplicar", action="store_true", help="Executa de verdade")
    args = ap.parse_args()
    dry = not args.aplicar or args.dry_run
    if not args.aplicar:
        dry = True

    print(f"\n  Modo: {'DRY-RUN' if dry else 'APLICAR'}\n")
    r1 = limpar_pdfs_sem_inaja(meses=args.meses, dry_run=dry)
    print(f"  PDFs sem Inajá anteriores a {r1['piso']}:")
    print(f"    arquivos={r1['arquivos']}  ~{r1['mb']} MB")
    for line in r1["itens"][:15]:
        print(f"    · {line}")
    r2 = reter_backups(manter=args.backups, dry_run=dry)
    print(f"\n  Backups: manter={r2['mantidos']} remover={r2['removidos']}")
    for n in r2.get("lista_removidos") or []:
        print(f"    · {n}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
