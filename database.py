from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from config import SETTINGS

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS edicoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT UNIQUE NOT NULL,
  titulo TEXT,
  data_publicacao TEXT,
  caminho_local TEXT,
  hash_md5 TEXT,
  texto_extraido_path TEXT,
  ocr_processado INTEGER DEFAULT 0,
  tem_inaja INTEGER DEFAULT 0,
  criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mencoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edicao_id INTEGER REFERENCES edicoes(id),
  pagina INTEGER,
  trecho TEXT,
  termo_encontrado TEXT,
  hash_trecho TEXT,
  notificado INTEGER DEFAULT 0,
  criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(edicao_id, hash_trecho)
);

CREATE TABLE IF NOT EXISTS publicacoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edicao_id INTEGER REFERENCES edicoes(id),
  pagina INTEGER,
  bloco INTEGER,
  categoria TEXT,
  orgao TEXT,
  tipo TEXT,
  numero TEXT,
  data_documento TEXT,
  assunto TEXT,
  valor TEXT,
  trecho TEXT,
  criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edicao_id INTEGER REFERENCES edicoes(id),
  titulo TEXT,
  etapa TEXT NOT NULL,
  status TEXT NOT NULL,
  mensagem TEXT,
  progress_current INTEGER,
  progress_total INTEGER,
  progress_step TEXT,
  iniciado_em TEXT DEFAULT CURRENT_TIMESTAMP,
  atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,
  finalizado_em TEXT
);

CREATE TABLE IF NOT EXISTS notificacoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edicao_id INTEGER REFERENCES edicoes(id),
  canal TEXT NOT NULL,
  conteudo TEXT,
  sucesso INTEGER DEFAULT 1,
  erro TEXT,
  criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webhooks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT UNIQUE NOT NULL,
  descricao TEXT,
  ativo INTEGER DEFAULT 1,
  criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_edicoes_url ON edicoes(url);
CREATE INDEX IF NOT EXISTS idx_mencoes_edicao ON mencoes(edicao_id);
CREATE INDEX IF NOT EXISTS idx_publicacoes_edicao ON publicacoes(edicao_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, atualizado_em);
CREATE INDEX IF NOT EXISTS idx_notificacoes_edicao ON notificacoes(edicao_id);

CREATE TABLE IF NOT EXISTS settings (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL,
  atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  aplicado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deteccao_metricas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edicao_id INTEGER REFERENCES edicoes(id),
  publicacoes_brutas INTEGER DEFAULT 0,
  publicacoes_finais INTEGER DEFAULT 0,
  descartes_ia INTEGER DEFAULT 0,
  descartes_vizinho INTEGER DEFAULT 0,
  paginas_total INTEGER DEFAULT 0,
  paginas_ocr_fraco INTEGER DEFAULT 0,
  mencoes INTEGER DEFAULT 0,
  criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deteccao_metricas_edicao ON deteccao_metricas(edicao_id);
"""


# Migrações versionadas: (version, sql)
# Adicione novas entradas ao final com version incrementado.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE publicacoes ADD COLUMN resumo_ia TEXT"),
    (2, "ALTER TABLE publicacoes ADD COLUMN categoria_ia TEXT"),
    (3, "ALTER TABLE publicacoes ADD COLUMN texto_corrigido TEXT"),
    (4, "ALTER TABLE publicacoes ADD COLUMN ia_processado INTEGER DEFAULT 0"),
    (5, "ALTER TABLE mencoes ADD COLUMN hash_trecho TEXT"),
    (6, "ALTER TABLE jobs ADD COLUMN progress_current INTEGER"),
    (7, "ALTER TABLE jobs ADD COLUMN progress_total INTEGER"),
    (8, "ALTER TABLE jobs ADD COLUMN progress_step TEXT"),
    # null | pendente | revisada | ignorada — edições com menção e 0 publicações
    (9, "ALTER TABLE edicoes ADD COLUMN revisao_so_mencao TEXT"),
]

# Colunas esperadas por migração — usadas para marcar versões já aplicadas
# em bancos antigos criados antes de schema_migrations existir.
_MIGRATION_MARKERS: dict[int, tuple[str, str]] = {
    1: ("publicacoes", "resumo_ia"),
    2: ("publicacoes", "categoria_ia"),
    3: ("publicacoes", "texto_corrigido"),
    4: ("publicacoes", "ia_processado"),
    5: ("mencoes", "hash_trecho"),
    6: ("jobs", "progress_current"),
    7: ("jobs", "progress_total"),
    8: ("jobs", "progress_step"),
    9: ("edicoes", "revisao_so_mencao"),
}


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path or SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _colunas_tabela(conn: sqlite3.Connection, tabela: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({tabela})").fetchall()
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    return {str(r[1]) for r in rows}


def _registrar_migracao(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, aplicado_em) VALUES (?, ?)",
        (version, datetime.now().isoformat(timespec="seconds")),
    )


def _sincronizar_migracoes_ja_aplicadas(conn: sqlite3.Connection) -> None:
    """Marca em schema_migrations as migrações cujos efeitos já existem no schema.

    Bancos criados antes do controle de versão ficavam com schema_migrations
    vazio mesmo com colunas (resumo_ia, etc.) já presentes.
    """
    for version, (tabela, coluna) in _MIGRATION_MARKERS.items():
        ja = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
        if ja:
            continue
        if coluna in _colunas_tabela(conn, tabela):
            _registrar_migracao(conn, version)
            logger.info(
                "Migração %s sincronizada (coluna %s.%s já existia)",
                version,
                tabela,
                coluna,
            )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _sincronizar_migracoes_ja_aplicadas(conn)

        aplicados = {
            int(r[0])
            for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, sql in _MIGRATIONS:
            if version in aplicados:
                continue
            try:
                conn.execute(sql)
                _registrar_migracao(conn, version)
                logger.info("Migração %s aplicada: %s", version, sql[:60])
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                # Coluna/tabela já existe → considera aplicada (idempotente)
                if "duplicate column" in msg or "already exists" in msg:
                    _registrar_migracao(conn, version)
                    logger.info(
                        "Migração %s já presente no schema, registrada: %s",
                        version,
                        exc,
                    )
                else:
                    logger.warning(
                        "Migração %s falhou (%s): %s", version, exc, sql[:60]
                    )


def pop_interrupted_edicao_ids() -> list[int]:
    """Retorna edições com jobs em execução e marca todos os jobs rodando como erro."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT edicao_id
            FROM jobs
            WHERE status = 'rodando' AND edicao_id IS NOT NULL
            ORDER BY edicao_id
            """
        ).fetchall()
        edicao_ids = [int(row["edicao_id"]) for row in rows]
        conn.execute(
            """
            UPDATE jobs
            SET status = 'erro',
                mensagem = 'Job interrompido - servidor reiniciado',
                finalizado_em = CURRENT_TIMESTAMP,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE status = 'rodando'
            """
        )
        return edicao_ids


def cleanup_stuck_jobs(max_hours: int = 2) -> int:
    """Marca como erro jobs que ficaram 'rodando' por mais de max_hours horas (ou todos se max_hours <= 0)."""
    with connect() as conn:
        if max_hours <= 0:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = 'erro',
                    mensagem = 'Job interrompido - servidor reiniciado',
                    finalizado_em = CURRENT_TIMESTAMP,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE status = 'rodando'
                """
            )
        else:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = 'erro',
                    mensagem = 'Job travado: marcado como erro automaticamente',
                    finalizado_em = CURRENT_TIMESTAMP,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE status = 'rodando'
                  AND atualizado_em < datetime('now', ? || ' hours')
                """,
                (f"-{max_hours}",),
            )
        return cur.rowcount


def get_setting(chave: str, padrao: str = "") -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT valor FROM settings WHERE chave = ?", (chave,)
        ).fetchone()
        return row["valor"] if row else padrao


def set_setting(chave: str, valor: str) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO settings (chave, valor, atualizado_em)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor,
                 atualizado_em = CURRENT_TIMESTAMP""",
            (chave, valor),
        )


def registrar_evento_ciclo(tipo: str, mensagem: str = "") -> None:
    """Registra fim de ciclo da WEB (varredura) ou do BOT (processamento).

    tipo: ``web_scan`` | ``bot_ciclo``
    """
    agora = datetime.now().isoformat(timespec="seconds")
    set_setting(f"ciclo_{tipo}_ultimo", agora)
    set_setting(f"ciclo_{tipo}_mensagem", mensagem or "")


def _formatar_dt_br(iso: str) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", ""))
        return dt.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def _proxima_a_partir(ultimo_iso: str, intervalo_h: int) -> tuple[str, str]:
    """Retorna (iso, rotulo_br) da próxima execução estimada."""
    horas = max(1, int(intervalo_h or 6))
    try:
        if ultimo_iso:
            base = datetime.fromisoformat(ultimo_iso.replace("Z", ""))
        else:
            base = datetime.now()
    except ValueError:
        base = datetime.now()
    from datetime import timedelta

    prox = base + timedelta(hours=horas)
    # Se a próxima já passou (processo reiniciou tarde), mostra "em breve / agora"
    if prox <= datetime.now():
        return (
            datetime.now().isoformat(timespec="seconds"),
            "em breve (intervalo vencido)",
        )
    return prox.isoformat(timespec="seconds"), prox.strftime("%d/%m/%Y %H:%M")


def get_status_automacao() -> dict:
    """Status unificado: última/próxima varredura (WEB) e ciclo do BOT + fila."""
    web_ultimo = get_setting("ciclo_web_scan_ultimo", "")
    web_msg = get_setting("ciclo_web_scan_mensagem", "")
    bot_ultimo = get_setting("ciclo_bot_ciclo_ultimo", "")
    bot_msg = get_setting("ciclo_bot_ciclo_mensagem", "")

    web_prox_iso, web_prox_br = _proxima_a_partir(
        web_ultimo, SETTINGS.web_scan_interval_hours
    )
    bot_prox_iso, bot_prox_br = _proxima_a_partir(
        bot_ultimo, SETTINGS.check_interval_hours
    )

    with connect() as conn:
        pendentes = int(
            conn.execute(
                "SELECT COUNT(*) FROM edicoes WHERE ocr_processado = 0"
            ).fetchone()[0]
            or 0
        )

    # Fila que o BOT pegaria no próximo ciclo (mesma regra de processar_pendentes)
    lim = max(0, int(SETTINGS.auto_process_limit or 0))
    dias = int(SETTINGS.auto_process_dias or 0)
    fila_proximo = len(
        get_pending_edicoes(
            process_all=False,
            limit=lim if lim else None,
            recent_days=dias if dias else None,
        )
    )

    return {
        "web_ultimo": web_ultimo,
        "web_ultimo_br": _formatar_dt_br(web_ultimo) if web_ultimo else "ainda não rodou",
        "web_mensagem": web_msg,
        "web_proxima": web_prox_iso,
        "web_proxima_br": web_prox_br if web_ultimo else f"a cada {SETTINGS.web_scan_interval_hours}h (após 1ª varredura)",
        "web_intervalo_h": int(SETTINGS.web_scan_interval_hours or 6),
        "bot_ultimo": bot_ultimo,
        "bot_ultimo_br": _formatar_dt_br(bot_ultimo) if bot_ultimo else "ainda não rodou",
        "bot_mensagem": bot_msg,
        "bot_proxima": bot_prox_iso,
        "bot_proxima_br": bot_prox_br if bot_ultimo else f"a cada {SETTINGS.check_interval_hours}h (após 1º ciclo)",
        "bot_intervalo_h": int(SETTINGS.check_interval_hours or 6),
        "pendentes_ocr": pendentes,
        "fila_proximo_ciclo": fila_proximo,
        "auto_process": bool(SETTINGS.auto_process),
        "auto_process_limit": int(SETTINGS.auto_process_limit or 0),
        "auto_process_dias": int(SETTINGS.auto_process_dias or 0),
    }


def url_exists(url: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM edicoes WHERE url = ?", (url,)).fetchone()
        return row is not None


def insert_or_get_edicao(url: str, titulo: str, data_publicacao: str | None) -> int:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO edicoes (url, titulo, data_publicacao)
            VALUES (?, ?, ?)
            """,
            (url, titulo, data_publicacao),
        )
        conn.execute(
            """
            UPDATE edicoes
            SET titulo = COALESCE(NULLIF(?, ''), titulo),
                data_publicacao = COALESCE(?, data_publicacao)
            WHERE url = ?
            """,
            (titulo, data_publicacao, url),
        )
        row = conn.execute("SELECT id FROM edicoes WHERE url = ?", (url,)).fetchone()
        if row is None:
            raise RuntimeError(f"Falha ao recuperar edição cadastrada: {url}")
        return int(row["id"])


def update_download(edicao_id: int, caminho: Path, tamanho: int, md5: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE edicoes
            SET caminho_local = ?, hash_md5 = ?
            WHERE id = ?
            """,
            (str(caminho), md5, edicao_id),
        )


def update_ocr(edicao_id: int, texto_path: Path, tem_inaja: bool | None = None) -> None:
    campos = ["texto_extraido_path = ?", "ocr_processado = 1"]
    valores: list[object] = [str(texto_path)]
    if tem_inaja is not None:
        campos.append("tem_inaja = ?")
        valores.append(1 if tem_inaja else 0)
    valores.append(edicao_id)
    with connect() as conn:
        conn.execute(f"UPDATE edicoes SET {', '.join(campos)} WHERE id = ?", valores)


def update_tem_inaja(edicao_id: int, tem_inaja: bool) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE edicoes SET tem_inaja = ? WHERE id = ?",
            (1 if tem_inaja else 0, edicao_id),
        )


def _hash_trecho(pagina: int, trecho: str, termo: str) -> str:
    """Gera hash único para deduplicar menções."""
    raw = f"{pagina}|{trecho}|{termo}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def insert_mencoes(edicao_id: int, mencoes: list[dict]) -> None:
    """Insere menções usando deduplicação por hash — preserva histórico de notificações."""
    with connect() as conn:
        # Remove apenas menções deste processo que nunca foram notificadas
        conn.execute(
            "DELETE FROM mencoes WHERE edicao_id = ? AND notificado = 0",
            (edicao_id,),
        )
        rows = [
            (
                edicao_id,
                item["pagina"],
                item["trecho"],
                item["termo"],
                _hash_trecho(item["pagina"], item["trecho"], item["termo"]),
            )
            for item in mencoes
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO mencoes (edicao_id, pagina, trecho, termo_encontrado, hash_trecho)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def insert_publicacoes(edicao_id: int, publicacoes: list[dict]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM publicacoes WHERE edicao_id = ?", (edicao_id,))
        conn.executemany(
            """
            INSERT INTO publicacoes (
              edicao_id, pagina, bloco, categoria, orgao, tipo, numero,
              data_documento, assunto, valor, trecho,
              resumo_ia, categoria_ia, texto_corrigido, ia_processado
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    edicao_id,
                    item.get("pagina"),
                    item.get("bloco"),
                    item.get("categoria"),
                    item.get("orgao"),
                    item.get("tipo"),
                    item.get("numero"),
                    item.get("data_documento"),
                    item.get("assunto"),
                    item.get("valor"),
                    item.get("trecho"),
                    item.get("resumo_ia"),
                    item.get("categoria_ia"),
                    item.get("texto_corrigido"),
                    1 if item.get("resumo_ia") or item.get("categoria_ia") else 0,
                )
                for item in publicacoes
            ],
        )


def start_job(
    etapa: str,
    titulo: str | None = None,
    edicao_id: int | None = None,
    mensagem: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    progress_step: str | None = None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (edicao_id, titulo, etapa, status, mensagem, progress_current, progress_total, progress_step)
            VALUES (?, ?, ?, 'rodando', ?, ?, ?, ?)
            """,
            (edicao_id, titulo, etapa, mensagem, progress_current, progress_total, progress_step),
        )
        return int(cur.lastrowid)


def update_job(
    job_id: int,
    status: str,
    mensagem: str | None = None,
    edicao_id: int | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    progress_step: str | None = None,
) -> None:
    finalizados = {"concluido", "erro", "ignorado"}
    finalizado_sql = ", finalizado_em = CURRENT_TIMESTAMP" if status in finalizados else ""
    edicao_sql = ", edicao_id = ?" if edicao_id is not None else ""
    prog_current_sql = ", progress_current = ?" if progress_current is not None else ""
    prog_total_sql = ", progress_total = ?" if progress_total is not None else ""
    prog_step_sql = ", progress_step = ?" if progress_step is not None else ""
    valores: list[object] = [status, mensagem]
    if edicao_id is not None:
        valores.append(edicao_id)
    if progress_current is not None:
        valores.append(progress_current)
    if progress_total is not None:
        valores.append(progress_total)
    if progress_step is not None:
        valores.append(progress_step)
    valores.append(job_id)
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE jobs
            SET status = ?, mensagem = ?, atualizado_em = CURRENT_TIMESTAMP
                {finalizado_sql}
                {edicao_sql}
                {prog_current_sql}
                {prog_total_sql}
                {prog_step_sql}
            WHERE id = ?
            """,
            valores,
        )


def log_job(
    etapa: str,
    status: str,
    titulo: str | None = None,
    edicao_id: int | None = None,
    mensagem: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    progress_step: str | None = None,
) -> int:
    job_id = start_job(etapa, titulo=titulo, edicao_id=edicao_id, mensagem=mensagem, progress_current=progress_current, progress_total=progress_total, progress_step=progress_step)
    update_job(job_id, status, mensagem=mensagem, edicao_id=edicao_id, progress_current=progress_current, progress_total=progress_total, progress_step=progress_step)
    return job_id


def mark_notified(edicao_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE mencoes SET notificado = 1 WHERE edicao_id = ?",
            (edicao_id,),
        )


def get_pending_edicoes(
    process_all: bool = False,
    *,
    limit: int | None = None,
    recent_days: int | None = None,
) -> list[sqlite3.Row]:
    """Edições ainda não processadas por OCR (ou todas se process_all).

    Ordena pelas mais recentes primeiro — essencial para automação com fila grande.
    """
    clauses: list[str] = []
    params: list[object] = []
    if not process_all:
        clauses.append("ocr_processado = 0")
    if recent_days and recent_days > 0:
        clauses.append(
            "(data_publicacao IS NULL OR data_publicacao >= date('now', ?))"
        )
        params.append(f"-{int(recent_days)} days")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    lim_sql = f" LIMIT {int(limit)}" if limit and limit > 0 else ""
    with connect() as conn:
        return list(
            conn.execute(
                f"""
                SELECT *
                FROM edicoes
                {where}
                ORDER BY
                  CASE WHEN data_publicacao IS NULL OR data_publicacao = '' THEN 1 ELSE 0 END,
                  data_publicacao DESC,
                  id DESC
                {lim_sql}
                """,
                params,
            )
        )


def reset_processing() -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE edicoes
            SET ocr_processado = 0, tem_inaja = 0, texto_extraido_path = NULL
            """
        )
        conn.execute("DELETE FROM mencoes WHERE notificado = 0")
        conn.execute("DELETE FROM publicacoes")


def insert_notificacao(
    edicao_id: int | None,
    canal: str,
    conteudo: str,
    sucesso: bool = True,
    erro: str | None = None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO notificacoes (edicao_id, canal, conteudo, sucesso, erro)
            VALUES (?, ?, ?, ?, ?)
            """,
            (edicao_id, canal, conteudo, 1 if sucesso else 0, erro),
        )
        return int(cur.lastrowid)


def get_notificacoes(limit: int = 100) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT n.*, e.titulo AS edicao_titulo, e.data_publicacao
                FROM notificacoes n
                LEFT JOIN edicoes e ON e.id = n.edicao_id
                ORDER BY n.id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def get_absence_alert_needed(days: int = 30) -> bool:
    """Retorna True se não houve publicação com Inajá nos últimos `days` dias."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM edicoes
            WHERE tem_inaja = 1
              AND data_publicacao >= date('now', ? || ' days')
            """,
            (f"-{days}",),
        ).fetchone()
        return (row["cnt"] if row else 0) == 0


def get_webhooks() -> list[sqlite3.Row]:
    with connect() as conn:
        return list(conn.execute("SELECT * FROM webhooks WHERE ativo = 1 ORDER BY id"))


def upsert_webhook(url: str, descricao: str = "", ativo: bool = True) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO webhooks (url, descricao, ativo)
            VALUES (?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              descricao = excluded.descricao,
              ativo = excluded.ativo
            """,
            (url, descricao, 1 if ativo else 0),
        )


def delete_webhook(webhook_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))


def get_publicacoes_por_mes() -> list[dict]:
    """Dados para gráfico de linha do tempo — publicações por mês."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
              substr(e.data_publicacao, 1, 7) AS mes,
              COUNT(p.id) AS total,
              SUM(CASE WHEN e.tem_inaja = 1 THEN 1 ELSE 0 END) AS com_inaja
            FROM edicoes e
            LEFT JOIN publicacoes p ON p.edicao_id = e.id
            WHERE e.data_publicacao IS NOT NULL
            GROUP BY mes
            ORDER BY mes DESC
            LIMIT 12
            """
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_timeline_por_mes() -> list[dict]:
    """Dados para timeline do dashboard — meses com contagem de atos e edições."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
              substr(e.data_publicacao, 1, 7) AS mes,
              COUNT(DISTINCT e.id)                                              AS edicoes,
              COUNT(DISTINCT CASE WHEN e.tem_inaja = 1 THEN e.id END)          AS edicoes_inaja,
              COUNT(p.id)                                                       AS pubs,
              GROUP_CONCAT(DISTINCT p.tipo)                                     AS tipos
            FROM edicoes e
            LEFT JOIN publicacoes p ON p.edicao_id = e.id
            WHERE e.data_publicacao IS NOT NULL
            GROUP BY mes
            ORDER BY mes DESC
            LIMIT 18
            """
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_publicacoes_por_tipo() -> list[dict]:
    """Dados para gráfico de pizza — publicações por tipo de ato."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
              COALESCE(tipo, 'Sem tipo') AS tipo,
              COUNT(*) AS total
            FROM publicacoes
            GROUP BY tipo
            ORDER BY total DESC
            LIMIT 10
            """
        ).fetchall()
        return [dict(r) for r in rows]


def clear_jobs_history() -> int:
    """Remove jobs antigos (concluídos, erro, ignorado) para limpar o histórico."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM jobs WHERE status IN ('concluido', 'erro', 'ignorado')"
        )
        return cur.rowcount


def delete_hnetsistemas_edicoes() -> int:
    """Remove edições antigas associadas ao domínio hnetsistemas.com.br."""
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM publicacoes
            WHERE edicao_id IN (SELECT id FROM edicoes WHERE url LIKE '%hnetsistemas.com.br%')
            """
        )
        conn.execute(
            """
            DELETE FROM mencoes
            WHERE edicao_id IN (SELECT id FROM edicoes WHERE url LIKE '%hnetsistemas.com.br%')
            """
        )
        conn.execute(
            """
            DELETE FROM jobs
            WHERE edicao_id IN (SELECT id FROM edicoes WHERE url LIKE '%hnetsistemas.com.br%')
            """
        )
        cur = conn.execute(
            "DELETE FROM edicoes WHERE url LIKE '%hnetsistemas.com.br%'"
        )
        return cur.rowcount


def salvar_arquivos_atos_locais(texto_path: Path | str, publicacoes: list[dict]) -> None:
    """Salva arquivos .atos.json e .atos.md na mesma pasta do PDF contendo apenas os atos daquela edição."""
    try:
        if not texto_path:
            return
        base_path = Path(texto_path).with_suffix("")
        json_path = Path(str(base_path) + ".atos.json")
        md_path = Path(str(base_path) + ".atos.md")

        # 1. Salvar JSON estruturado
        dados = []
        for p in publicacoes:
            dados.append({
                "pagina": p.get("pagina"),
                "orgao": p.get("orgao"),
                "tipo": p.get("tipo"),
                "numero": p.get("numero"),
                "data_documento": p.get("data_documento"),
                "valor": p.get("valor"),
                "assunto": p.get("assunto") or p.get("resumo_ia"),
                "resumo_ia": p.get("resumo_ia"),
                "trecho_ocr": p.get("trecho") or p.get("texto_corrigido")
            })
        
        json_path.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

        # 2. Salvar Markdown legível
        linhas = [
            f"# Atos Oficiais de Inajá-PR — {base_path.name}",
            f"Edição reprocessada/analisada em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
            f"Total de atos detectados: {len(publicacoes)}",
            "",
            "---",
            ""
        ]
        
        for idx, p in enumerate(publicacoes, 1):
            linhas.extend([
                f"## Ato {idx}: {p.get('tipo') or 'Ato Oficial'} {p.get('numero') or ''}",
                f"- **Órgão:** {p.get('orgao') or 'Não especificado'}",
                f"- **Data do Ato:** {p.get('data_documento') or 'Não informada'}",
                f"- **Página no Jornal:** {p.get('pagina') or 'N/A'}",
                f"- **Valor:** {p.get('valor') or '—'}",
                f"- **Resumo IA:** {p.get('resumo_ia') or p.get('assunto') or 'Não gerado'}",
                "",
                "### Trecho do Documento:",
                "```text",
                (p.get("trecho") or p.get("texto_corrigido") or "").strip(),
                "```",
                "",
                "---",
                ""
            ])
            
        md_path.write_text("\n".join(linhas), encoding="utf-8")
    except Exception:
        logger.exception("Falha ao salvar arquivos locais de atos para %s", texto_path)


def get_publicacoes_sem_ia(limit: int = 100) -> list[sqlite3.Row]:
    """Retorna publicações que ainda não foram refinadas pela IA.

    Considera pendentes aquelas com ``ia_processado = 0`` OU com
    ``resumo_ia`` nulo e ``tipo`` nulo (falha silenciosa).
    """
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT p.id, p.edicao_id, p.pagina, p.bloco, p.categoria, p.orgao,
                   p.tipo, p.numero, p.data_documento, p.assunto, p.valor,
                   p.trecho, p.resumo_ia, p.categoria_ia, p.texto_corrigido,
                   p.ia_processado
            FROM publicacoes p
            WHERE p.ia_processado = 0
               OR p.resumo_ia IS NULL
               OR p.resumo_ia = ''
            ORDER BY p.edicao_id, p.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def update_publicacao_ia(pub: dict) -> None:
    """Atualiza os campos refinados pela IA em uma publicação existente."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE publicacoes
            SET orgao           = COALESCE(?, orgao),
                tipo            = COALESCE(?, tipo),
                numero          = COALESCE(?, numero),
                data_documento  = COALESCE(?, data_documento),
                assunto         = COALESCE(?, assunto),
                valor           = COALESCE(?, valor),
                resumo_ia       = ?,
                categoria_ia    = COALESCE(?, categoria_ia),
                texto_corrigido = COALESCE(?, texto_corrigido),
                ia_processado   = 1
            WHERE id = ?
            """,
            (
                pub.get("orgao"),
                pub.get("tipo"),
                pub.get("numero"),
                pub.get("data_documento"),
                pub.get("assunto"),
                pub.get("valor"),
                pub.get("resumo_ia"),
                pub.get("categoria_ia"),
                pub.get("texto_corrigido"),
                pub["id"],
            ),
        )


def salvar_metricas_deteccao(
    edicao_id: int,
    metricas: dict[str, int] | object,
) -> None:
    """Persiste métricas de uma execução de detecção (substitui a da edição)."""
    if hasattr(metricas, "as_dict"):
        data = metricas.as_dict()  # type: ignore[union-attr]
    else:
        data = dict(metricas)  # type: ignore[arg-type]
    with connect() as conn:
        conn.execute("DELETE FROM deteccao_metricas WHERE edicao_id = ?", (edicao_id,))
        conn.execute(
            """
            INSERT INTO deteccao_metricas (
              edicao_id, publicacoes_brutas, publicacoes_finais,
              descartes_ia, descartes_vizinho, paginas_total,
              paginas_ocr_fraco, mencoes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edicao_id,
                int(data.get("publicacoes_brutas", 0)),
                int(data.get("publicacoes_finais", 0)),
                int(data.get("descartes_ia", 0)),
                int(data.get("descartes_vizinho", 0)),
                int(data.get("paginas_total", 0)),
                int(data.get("paginas_ocr_fraco", 0)),
                int(data.get("mencoes", 0)),
            ),
        )


def parse_valor_monetario(valor: str | None) -> float | None:
    """Converte 'R$ 1.234,56' em float. Retorna None se inválido."""
    if not valor:
        return None
    s = (
        str(valor)
        .replace("R$", "")
        .replace("r$", "")
        .replace(" ", "")
        .strip()
    )
    if not s:
        return None
    # Formato BR: 1.234.567,89
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def somar_valores_publicacoes(
    *,
    deduplicar: bool = True,
    excluir_materias: bool = True,
) -> dict[str, float | int]:
    """Soma valores citados nas publicações.

    Com ``deduplicar=True`` (padrão), mantém no máximo um valor por chave
    (órgão + tipo + número) ou, na ausência de número, por (órgão + tipo + valor).
    Assim aviso de licitação e extrato do mesmo contrato não somam duas vezes
    o mesmo montante quando compartilham tipo/número normalizados.

    Com ``excluir_materias=True`` (padrão), ignora categoria materia_jornalistica
    (valores de reportagem, não de ato formal).
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT orgao, tipo, numero, valor, categoria, trecho
            FROM publicacoes
            WHERE valor IS NOT NULL AND valor != ''
            """
        ).fetchall()

    brutos: list[tuple[str, float]] = []
    for r in rows:
        cat = (r["categoria"] or "").strip().casefold()
        if excluir_materias and "materia" in cat:
            continue
        v = parse_valor_monetario(r["valor"])
        if v is None:
            continue
        orgao = (r["orgao"] or "").strip().casefold()
        tipo = (r["tipo"] or "").strip().casefold()
        numero = (r["numero"] or "").strip().casefold()
        # Preferência: número do ato; senão tenta processo no trecho
        if not numero and r["trecho"]:
            m_proc = re.search(
                r"processo\s*n[º°o.]?\s*(\d{1,6}(?:[./-]\d{1,6})?)",
                (r["trecho"] or ""),
                re.I,
            )
            if m_proc:
                numero = f"proc:{m_proc.group(1).casefold()}"
        if numero:
            # Mesmo número de ato, tipos diferentes (aviso vs extrato) → uma chave por número+órgão
            chave = f"{orgao}|{numero}"
        else:
            chave = f"{orgao}|{tipo}|{v:.2f}"
        brutos.append((chave, v))

    total_bruto = sum(v for _, v in brutos)
    if deduplicar:
        unicos: dict[str, float] = {}
        for chave, v in brutos:
            # Mantém o maior valor da chave (em caso de divergência OCR)
            if chave not in unicos or v > unicos[chave]:
                unicos[chave] = v
        total = sum(unicos.values())
        n_unicos = len(unicos)
    else:
        total = total_bruto
        n_unicos = len(brutos)

    return {
        "total": total,
        "total_bruto": total_bruto,
        "n_com_valor": len(brutos),
        "n_unicos": n_unicos,
        "deduplicado": 1 if deduplicar else 0,
    }


def formatar_reais(valor: float) -> str:
    if valor >= 1_000_000.0:
        return f"R$ {valor / 1_000_000.0:.2f}M"
    if valor > 0:
        return (
            f"R$ {valor:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    return "R$ 0,00"


def normalizar_tipos_publicacoes_existentes() -> int:
    """Reescreve tipos já gravados com o normalizador canônico. Retorna qtd alterada."""
    from ai_processor import normalizar_tipo_ato

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, tipo FROM publicacoes WHERE tipo IS NOT NULL AND tipo != ''"
        ).fetchall()
        alterados = 0
        for row in rows:
            novo = normalizar_tipo_ato(row["tipo"])
            if novo and novo != row["tipo"]:
                conn.execute(
                    "UPDATE publicacoes SET tipo = ? WHERE id = ?",
                    (novo, row["id"]),
                )
                alterados += 1
        return alterados


def get_metricas_qualidade() -> dict[str, float | int]:
    """Agrega métricas de detecção e status de IA para o dashboard."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(publicacoes_brutas), 0) AS publicacoes_brutas,
              COALESCE(SUM(publicacoes_finais), 0) AS publicacoes_finais,
              COALESCE(SUM(descartes_ia), 0) AS descartes_ia,
              COALESCE(SUM(descartes_vizinho), 0) AS descartes_vizinho,
              COALESCE(SUM(paginas_total), 0) AS paginas_total,
              COALESCE(SUM(paginas_ocr_fraco), 0) AS paginas_ocr_fraco,
              COALESCE(SUM(mencoes), 0) AS mencoes_metricas,
              COUNT(*) AS edicoes_com_metricas
            FROM deteccao_metricas
            """
        ).fetchone()
        ia = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN ia_processado = 1 THEN 1 ELSE 0 END) AS com_ia,
              SUM(CASE WHEN ia_processado = 0 OR ia_processado IS NULL THEN 1 ELSE 0 END) AS sem_ia
            FROM publicacoes
            """
        ).fetchone()

    brutas = int(row["publicacoes_brutas"] or 0)
    finais = int(row["publicacoes_finais"] or 0)
    paginas = int(row["paginas_total"] or 0)
    ocr_fraco = int(row["paginas_ocr_fraco"] or 0)
    total_pub = int(ia["total"] or 0)
    com_ia = int(ia["com_ia"] or 0)

    taxa_retencao = (finais / brutas * 100.0) if brutas else 0.0
    taxa_ia = (com_ia / total_pub * 100.0) if total_pub else 0.0
    taxa_ocr_fraco = (ocr_fraco / paginas * 100.0) if paginas else 0.0

    return {
        "edicoes_com_metricas": int(row["edicoes_com_metricas"] or 0),
        "publicacoes_brutas": brutas,
        "publicacoes_finais": finais,
        "descartes_ia": int(row["descartes_ia"] or 0),
        "descartes_vizinho": int(row["descartes_vizinho"] or 0),
        "paginas_total": paginas,
        "paginas_ocr_fraco": ocr_fraco,
        "taxa_retencao_pct": round(taxa_retencao, 1),
        "taxa_ia_ok_pct": round(taxa_ia, 1),
        "taxa_ocr_fraco_pct": round(taxa_ocr_fraco, 1),
        "publicacoes_com_ia": com_ia,
        "publicacoes_sem_ia": int(ia["sem_ia"] or 0),
        "publicacoes_total": total_pub,
    }


def listar_edicoes_so_mencao(
    *,
    incluir_revisadas: bool = False,
    limit: int = 200,
) -> list[sqlite3.Row]:
    """Edições com tem_inaja=1 e zero publicações (candidatas a FN ou menção legítima)."""
    filtro_revisao = ""
    if not incluir_revisadas:
        filtro_revisao = (
            "AND (e.revisao_so_mencao IS NULL OR e.revisao_so_mencao = '' "
            "OR e.revisao_so_mencao = 'pendente')"
        )
    with connect() as conn:
        return list(
            conn.execute(
                f"""
                SELECT e.id, e.titulo, e.data_publicacao, e.url, e.ocr_processado,
                       e.revisao_so_mencao,
                       (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) AS mencoes_count,
                       (SELECT GROUP_CONCAT(DISTINCT m2.termo_encontrado)
                          FROM mencoes m2 WHERE m2.edicao_id = e.id) AS termos
                FROM edicoes e
                WHERE e.tem_inaja = 1
                  AND NOT EXISTS (
                    SELECT 1 FROM publicacoes p WHERE p.edicao_id = e.id
                  )
                  {filtro_revisao}
                ORDER BY e.data_publicacao DESC, e.id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def marcar_revisao_so_mencao(edicao_id: int, status: str) -> None:
    """status: pendente | revisada | ignorada"""
    status = (status or "pendente").strip().casefold()
    if status not in {"pendente", "revisada", "ignorada"}:
        raise ValueError(f"Status de revisão inválido: {status}")
    with connect() as conn:
        conn.execute(
            "UPDATE edicoes SET revisao_so_mencao = ? WHERE id = ?",
            (status, edicao_id),
        )


def backup_database(destino_dir: Path | None = None) -> Path:
    """Cópia de segurança do SQLite (usa backup API; seguro com WAL)."""
    dest_dir = destino_dir or (SETTINGS.log_dir / "backups")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = dest_dir / f"jornal_monitor_{stamp}.db"
    src = str(SETTINGS.db_path)
    with sqlite3.connect(src) as src_conn:
        with sqlite3.connect(str(dest)) as dst_conn:
            src_conn.backup(dst_conn)
    logger.info("Backup SQLite gravado em %s", dest)
    return dest

