# -*- coding: utf-8 -*-
"""Consulta/altera settings de runtime no banco + mostra .env relevantes.

Uso:
  python scripts/_settings_cli.py
  python scripts/_settings_cli.py --set ai_refine_publications=false
  python scripts/_settings_cli.py --toggle-ia
  python scripts/_settings_cli.py --list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SETTINGS
import database

database.init_db()

KEYS_INTERESSE = [
    "opencode_api_key",
    "openrouter_api_key",
    "ai_refine_publications",
    "telegram_bot_token",
    "telegram_chat_id",
    "smtp_host",
    "termos_extra",
    "detection_terms",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--set", dest="set_kv", help="chave=valor no settings DB")
    ap.add_argument("--toggle-ia", action="store_true")
    args = ap.parse_args()

    print("\n  === Settings (.env em memoria) ===")
    print(f"  AI refine:     {SETTINGS.ai_refine_publications}")
    print(f"  Modelo:        {SETTINGS.opencode_model}")
    print(f"  Intervalo BOT: {SETTINGS.check_interval_hours}h")
    print(f"  Auto process:  {SETTINGS.auto_process}")
    print(f"  Limit lote:    {SETTINGS.auto_process_limit}")
    print(f"  Telegram:      {bool(SETTINGS.telegram_bot_token and SETTINGS.telegram_chat_id)}")
    print(f"  DB:            {SETTINGS.db_path}")

    print("\n  === Settings (tabela settings) ===")
    with database.connect() as c:
        rows = c.execute(
            "SELECT chave, valor, atualizado_em FROM settings ORDER BY chave"
        ).fetchall()
    if not rows:
        print("  (vazia)")
    else:
        for r in rows:
            val = r["valor"] or ""
            chave = r["chave"]
            if "key" in chave.lower() or "token" in chave.lower() or "password" in chave.lower():
                shown = ("…" + val[-6:]) if len(val) > 6 else ("***" if val else "—")
            else:
                shown = val[:80] + ("…" if len(val) > 80 else "")
            print(f"  {chave}: {shown}")

    if args.toggle_ia:
        cur = database.get_setting("ai_refine_publications", "")
        if cur == "":
            # fallback: invert SETTINGS
            new = "false" if SETTINGS.ai_refine_publications else "true"
        else:
            new = "false" if cur.strip().lower() in {"1", "true", "sim", "yes", "on"} else "true"
        database.set_setting("ai_refine_publications", new)
        object.__setattr__(
            SETTINGS,
            "ai_refine_publications",
            new.lower() in {"1", "true", "sim", "yes", "on"},
        )
        print(f"\n  AI refine agora: {new} (sessao + settings DB)")
        print("  Nota: processos ja rodando precisam reiniciar para pegar .env;")
        print("  este processo e scripts filhos usam o valor em memoria se setado.")

    if args.set_kv:
        if "=" not in args.set_kv:
            print("--set precisa chave=valor")
            return 2
        k, v = args.set_kv.split("=", 1)
        k, v = k.strip(), v.strip()
        database.set_setting(k, v)
        print(f"\n  Gravado settings: {k}={v[:40]}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
