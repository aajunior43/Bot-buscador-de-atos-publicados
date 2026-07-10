# -*- coding: utf-8 -*-
"""Status resumido da fila / banco + últimas edições + automação."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from process_lock import DEFAULT_LOCK

database.init_db()
with database.connect() as conn:
    tot = conn.execute("SELECT COUNT(*) FROM edicoes").fetchone()[0]
    pend = conn.execute(
        "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 0"
    ).fetchone()[0]
    ok = conn.execute(
        "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 1"
    ).fetchone()[0]
    inaja = conn.execute(
        "SELECT COUNT(*) FROM edicoes WHERE tem_inaja = 1"
    ).fetchone()[0]
    pubs = conn.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
    men = conn.execute("SELECT COUNT(*) FROM mencoes").fetchone()[0]
    jobs_r = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'rodando'"
    ).fetchone()[0]
    jobs_e = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'erro'"
    ).fetchone()[0]
    recentes = conn.execute(
        """
        SELECT data_publicacao, titulo, ocr_processado, tem_inaja,
               (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) n_pub
        FROM edicoes e
        ORDER BY data_publicacao DESC, id DESC
        LIMIT 10
        """
    ).fetchall()
    top_meses = conn.execute(
        """
        SELECT substr(data_publicacao,1,7) mes,
               SUM(CASE WHEN ocr_processado=0 THEN 1 ELSE 0 END) pend,
               SUM(CASE WHEN tem_inaja=1 THEN 1 ELSE 0 END) ina,
               COUNT(*) tot
        FROM edicoes
        WHERE data_publicacao IS NOT NULL AND data_publicacao != ''
        GROUP BY substr(data_publicacao,1,7)
        ORDER BY mes DESC
        LIMIT 6
        """
    ).fetchall()

print()
print("  === Fila / banco ===")
print(f"  Edicoes:      {tot}")
print(f"  Processadas:  {ok}")
print(f"  Pendentes:    {pend}")
print(f"  Com Inaja:    {inaja}")
print(f"  Publicacoes:  {pubs}")
print(f"  Mencoes:      {men}")
print(f"  Jobs rodando: {jobs_r}")
print(f"  Jobs erro:    {jobs_e}")
print(f"  Lock file:    {'SIM — ' + str(DEFAULT_LOCK) if DEFAULT_LOCK.exists() else 'nao'}")

try:
    st = database.get_status_automacao()
    print()
    print("  === Automacao ===")
    print(f"  BOT vivo:     {bool(st.get('bot_vivo'))}")
    print(f"  Fila ciclo:   {st.get('fila_proximo_ciclo')}")
    print(f"  Quarentena:   {st.get('quarentena_count')}")
    print(f"  Ult. BOT:     {st.get('bot_ultimo') or '—'}")
    print(f"  Heartbeat:    {st.get('bot_heartbeat_br') or '—'}")
except Exception:
    pass

print()
print("  === Pendencias por mes (6) ===")
for r in top_meses:
    print(
        f"  {r['mes'] or '????-??'}  total={r['tot']:4}  "
        f"pend={r['pend'] or 0:4}  inaja={r['ina'] or 0:3}"
    )

print()
print("  === Ultimas edicoes ===")
for r in recentes:
    flag = "OK " if r["ocr_processado"] else "PEND"
    ina = "INAJA" if r["tem_inaja"] else "    "
    print(
        f"  [{flag}] {r['data_publicacao'] or '????-??-??'}  "
        f"{ina}  pubs={r['n_pub']:2}  {r['titulo'] or '—'}"
    )
print()
