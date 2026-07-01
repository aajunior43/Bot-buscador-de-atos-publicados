from __future__ import annotations

import hashlib
import json
import logging
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
"""


# Migrações versionadas: (version, sql)
# Adicione novas entradas ao final com version incrementado.
_MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE publicacoes ADD COLUMN resumo_ia TEXT"),
    (2, "ALTER TABLE publicacoes ADD COLUMN categoria_ia TEXT"),
    (3, "ALTER TABLE publicacoes ADD COLUMN texto_corrigido TEXT"),
    (4, "ALTER TABLE publicacoes ADD COLUMN ia_processado INTEGER DEFAULT 0"),
    (5, "ALTER TABLE mencoes ADD COLUMN hash_trecho TEXT"),
]


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


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Descobrir a versão atual do schema
        versao_atual: int = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        # Aplicar apenas migrações com versão superior à atual
        pendentes = [(v, sql) for v, sql in _MIGRATIONS if v > versao_atual]
        for version, sql in pendentes:
            try:
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, aplicado_em) VALUES (?, ?)",
                    (version, datetime.now().isoformat(timespec="seconds")),
                )
                logger.info("Migração %s aplicada: %s", version, sql[:60])
            except sqlite3.OperationalError as exc:
                logger.warning(
                    "Migração %s ignorada (%s): %s", version, exc, sql[:60]
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
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (edicao_id, titulo, etapa, status, mensagem)
            VALUES (?, ?, ?, 'rodando', ?)
            """,
            (edicao_id, titulo, etapa, mensagem),
        )
        return int(cur.lastrowid)


def update_job(
    job_id: int,
    status: str,
    mensagem: str | None = None,
    edicao_id: int | None = None,
) -> None:
    finalizados = {"concluido", "erro", "ignorado"}
    finalizado_sql = ", finalizado_em = CURRENT_TIMESTAMP" if status in finalizados else ""
    edicao_sql = ", edicao_id = ?" if edicao_id is not None else ""
    valores: list[object] = [status, mensagem]
    if edicao_id is not None:
        valores.append(edicao_id)
    valores.append(job_id)
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE jobs
            SET status = ?, mensagem = ?, atualizado_em = CURRENT_TIMESTAMP
                {finalizado_sql}
                {edicao_sql}
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
) -> int:
    job_id = start_job(etapa, titulo=titulo, edicao_id=edicao_id, mensagem=mensagem)
    update_job(job_id, status, mensagem=mensagem, edicao_id=edicao_id)
    return job_id


def mark_notified(edicao_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE mencoes SET notificado = 1 WHERE edicao_id = ?",
            (edicao_id,),
        )


def get_pending_edicoes(process_all: bool = False) -> list[sqlite3.Row]:
    where = "" if process_all else "WHERE ocr_processado = 0"
    with connect() as conn:
        return list(
            conn.execute(
                f"""
                SELECT *
                FROM edicoes
                {where}
                ORDER BY id ASC
                """
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



