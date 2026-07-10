# -*- coding: utf-8 -*-
"""Mostra status da IA / chaves / flags principais + contagem no banco."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SETTINGS
import database

database.init_db()

try:
    from ai_processor import ia_disponivel, _api_key, _auth_bloqueada, ai_calls_no_ciclo
except Exception as exc:
    print(f"  Erro ao importar ai_processor: {exc}")
    sys.exit(1)

key = _api_key()
print()
print("  === Status da IA ===")
print(f"  Disponivel:     {ia_disponivel()}")
print(
    f"  Chave presente: {bool(key)}  "
    f"({'…' + key[-6:] if key and len(key) > 6 else '—'})"
)
print(f"  Auth bloqueada: {_auth_bloqueada}")
print(f"  Modelo:         {SETTINGS.opencode_model}")
url = SETTINGS.opencode_api_url or ""
print(f"  URL:            {(url[:64] + '…') if len(url) > 64 else url or '—'}")
print(f"  Refine:         {SETTINGS.ai_refine_publications}")
print(f"  Importancia:    {getattr(SETTINGS, 'ai_importancia', True)}")
print(f"  Chat:           {getattr(SETTINGS, 'ai_chat', True)}")
print(f"  Max calls/ciclo:{getattr(SETTINGS, 'ai_max_calls_por_ciclo', '?')}")
print(f"  Calls neste proc: {ai_calls_no_ciclo()}")

with database.connect() as c:
    com_resumo = c.execute(
        "SELECT COUNT(*) FROM publicacoes "
        "WHERE resumo_ia IS NOT NULL AND trim(resumo_ia) != ''"
    ).fetchone()[0]
    total_p = c.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
    com_valor = c.execute(
        "SELECT COUNT(*) FROM publicacoes "
        "WHERE valor IS NOT NULL AND trim(valor) != ''"
    ).fetchone()[0]

print()
print("  === No banco ===")
print(f"  Publicacoes:    {total_p}")
print(f"  Com resumo_ia:  {com_resumo}")
print(f"  Com valor:      {com_valor}")
print()
print(
    "  Telegram:       ",
    bool(SETTINGS.telegram_bot_token and SETTINGS.telegram_chat_id),
)
print(
    "  Email SMTP:     ",
    bool(getattr(SETTINGS, "smtp_host", None) or getattr(SETTINGS, "email_smtp_host", None)),
)
print()
