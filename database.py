from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import SETTINGS


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
  notificado INTEGER DEFAULT 0,
  criado_em TEXT DEFAULT CURRENT_TIMESTAMP
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

CREATE INDEX IF NOT EXISTS idx_edicoes_url ON edicoes(url);
CREATE INDEX IF NOT EXISTS idx_mencoes_edicao ON mencoes(edicao_id);
CREATE INDEX IF NOT EXISTS idx_publicacoes_edicao ON publicacoes(edicao_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, atualizado_em);
"""


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path or SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
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


def insert_mencoes(edicao_id: int, mencoes: list[dict]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM mencoes WHERE edicao_id = ?", (edicao_id,))
        conn.executemany(
            """
            INSERT INTO mencoes (edicao_id, pagina, trecho, termo_encontrado)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    edicao_id,
                    item["pagina"],
                    item["trecho"],
                    item["termo"],
                )
                for item in mencoes
            ],
        )


def insert_publicacoes(edicao_id: int, publicacoes: list[dict]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM publicacoes WHERE edicao_id = ?", (edicao_id,))
        conn.executemany(
            """
            INSERT INTO publicacoes (
              edicao_id, pagina, bloco, categoria, orgao, tipo, numero,
              data_documento, assunto, valor, trecho
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        conn.execute("DELETE FROM mencoes")
        conn.execute("DELETE FROM publicacoes")
