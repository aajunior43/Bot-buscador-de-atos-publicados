# -*- coding: utf-8 -*-
"""Uma linha de status para o cabeçalho do menu CMD."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import database
    from config import SETTINGS
    from process_lock import DEFAULT_LOCK

    database.init_db()
    with database.connect() as conn:
        pend = conn.execute(
            "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 0"
        ).fetchone()[0]
        pubs = conn.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
        ina = conn.execute(
            "SELECT COUNT(*) FROM edicoes WHERE tem_inaja = 1"
        ).fetchone()[0]
        jobs = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'rodando'"
        ).fetchone()[0]

    lock_on = "LOCK" if DEFAULT_LOCK.exists() else "livre"
    bot = ""
    try:
        st = database.get_status_automacao()
        bot = "  ·  BOT vivo" if st.get("bot_vivo") else "  ·  BOT parado"
    except Exception:
        pass

    agente = ""
    try:
        from agente import agente_esta_ativo, modo_efetivo, resolver_modo_auto

        if agente_esta_ativo():
            m = modo_efetivo()
            me = resolver_modo_auto() if m == "auto" else m
            agente = f"  ·  AGENTE on/{me}"
        else:
            agente = "  ·  AGENTE off"
    except Exception:
        pass

    extra = f"  ·  jobs={jobs}" if jobs else ""
    print(
        f"  Fila: {pend} pendentes  |  {pubs} publicacoes  |  "
        f"{ina} c/ Inaja  |  lock={lock_on}{extra}{bot}{agente}"
    )
except Exception:
    print("  (status indisponivel)")
    sys.exit(0)
