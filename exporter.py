from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

from config import SETTINGS


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
    return conn


_CAMPOS_CSV = [
    "id",
    "edicao_id",
    "edicao_titulo",
    "data_publicacao",
    "pagina",
    "categoria",
    "orgao",
    "tipo",
    "numero",
    "data_documento",
    "assunto",
    "valor",
    "resumo_ia",
    "importancia",
    "confianca",
    "confianca_nivel",
    "flags_qualidade",
    "criado_em",
]


def _buscar_publicacoes(
    data_inicio: str | None = None,
    data_fim: str | None = None,
    tipo: str | None = None,
    orgao: str | None = None,
    apenas_inaja: bool = False,
    limit: int = 5000,
) -> list[dict]:
    filtros: list[str] = []
    params: list[object] = []

    if data_inicio:
        filtros.append("e.data_publicacao >= ?")
        params.append(data_inicio)
    if data_fim:
        filtros.append("e.data_publicacao <= ?")
        params.append(data_fim)
    if tipo:
        filtros.append("p.tipo LIKE ?")
        params.append(f"%{tipo}%")
    if orgao:
        filtros.append("p.orgao LIKE ?")
        params.append(f"%{orgao}%")
    if apenas_inaja:
        filtros.append("e.tem_inaja = 1")

    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    params.append(limit)

    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
              p.id, p.edicao_id, e.titulo AS edicao_titulo, e.data_publicacao,
              p.pagina, p.categoria, p.orgao, p.tipo, p.numero,
              p.data_documento, p.assunto, p.valor, p.resumo_ia, p.criado_em
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            {where}
            ORDER BY e.data_publicacao DESC, p.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def exportar_csv(
    data_inicio: str | None = None,
    data_fim: str | None = None,
    tipo: str | None = None,
    orgao: str | None = None,
    apenas_inaja: bool = False,
) -> str:
    """Retorna string CSV com publicações filtradas."""
    rows = _buscar_publicacoes(data_inicio, data_fim, tipo, orgao, apenas_inaja)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=_CAMPOS_CSV,
        extrasaction="ignore",
        delimiter=";",
        quoting=csv.QUOTE_ALL,
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def exportar_json(
    data_inicio: str | None = None,
    data_fim: str | None = None,
    tipo: str | None = None,
    orgao: str | None = None,
    apenas_inaja: bool = False,
) -> list[dict]:
    """Retorna lista de dicts com publicações filtradas."""
    return _buscar_publicacoes(data_inicio, data_fim, tipo, orgao, apenas_inaja)
