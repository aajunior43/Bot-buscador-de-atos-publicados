from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pdf2image import convert_from_path
from starlette.requests import Request

from config import SETTINGS
from database import init_db


BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Monitor O Regional - Inajá")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_or_404(conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Row:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Registro não encontrado")
    return row


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = Query("", description="Busca por título, data, órgão, tipo ou assunto"),
) -> HTMLResponse:
    termo = f"%{q.strip()}%"
    with _conn() as conn:
        stats = conn.execute(
            """
            SELECT
              COUNT(*) AS total_edicoes,
              SUM(CASE WHEN tem_inaja = 1 THEN 1 ELSE 0 END) AS edicoes_inaja,
              (SELECT COUNT(*) FROM publicacoes) AS total_publicacoes,
              (SELECT COUNT(*) FROM mencoes) AS total_mencoes
            FROM edicoes
            """
        ).fetchone()

        if q.strip():
            edicoes = conn.execute(
                """
                SELECT DISTINCT e.*,
                  (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) AS publicacoes_count,
                  (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) AS mencoes_count
                FROM edicoes e
                LEFT JOIN publicacoes p ON p.edicao_id = e.id
                WHERE e.titulo LIKE ?
                   OR e.data_publicacao LIKE ?
                   OR p.orgao LIKE ?
                   OR p.tipo LIKE ?
                   OR p.assunto LIKE ?
                ORDER BY e.data_publicacao DESC, e.id DESC
                LIMIT 100
                """,
                (termo, termo, termo, termo, termo),
            ).fetchall()
        else:
            edicoes = conn.execute(
                """
                SELECT e.*,
                  (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) AS publicacoes_count,
                  (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) AS mencoes_count
                FROM edicoes e
                ORDER BY e.data_publicacao DESC, e.id DESC
                LIMIT 100
                """
            ).fetchall()

        publicacoes = conn.execute(
            """
            SELECT p.*, e.titulo AS edicao_titulo, e.data_publicacao
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            ORDER BY e.data_publicacao DESC, p.id DESC
            LIMIT 30
            """
        ).fetchall()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "edicoes": edicoes,
            "publicacoes": publicacoes,
            "q": q,
        },
    )


@app.get("/edicoes/{edicao_id}", response_class=HTMLResponse)
def edicao_detail(request: Request, edicao_id: int) -> HTMLResponse:
    with _conn() as conn:
        edicao = _row_or_404(conn, "SELECT * FROM edicoes WHERE id = ?", (edicao_id,))
        publicacoes = conn.execute(
            """
            SELECT *
            FROM publicacoes
            WHERE edicao_id = ?
            ORDER BY pagina, bloco, id
            """,
            (edicao_id,),
        ).fetchall()
        mencoes = conn.execute(
            """
            SELECT *
            FROM mencoes
            WHERE edicao_id = ?
            ORDER BY pagina, id
            """,
            (edicao_id,),
        ).fetchall()

    return templates.TemplateResponse(
        "edicao.html",
        {
            "request": request,
            "edicao": edicao,
            "publicacoes": publicacoes,
            "mencoes": mencoes,
        },
    )


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request) -> HTMLResponse:
    with _conn() as conn:
        resumo = conn.execute(
            """
            SELECT
              SUM(CASE WHEN status = 'rodando' THEN 1 ELSE 0 END) AS rodando,
              SUM(CASE WHEN status = 'aviso' THEN 1 ELSE 0 END) AS avisos,
              SUM(CASE WHEN status = 'erro' THEN 1 ELSE 0 END) AS erros,
              SUM(CASE WHEN status = 'concluido' THEN 1 ELSE 0 END) AS concluidos,
              COUNT(*) AS total
            FROM jobs
            """
        ).fetchone()
        jobs = conn.execute(
            """
            SELECT j.*, e.data_publicacao, e.url
            FROM jobs j
            LEFT JOIN edicoes e ON e.id = j.edicao_id
            ORDER BY j.id ASC
            """
        ).fetchall()

    grupos: list[dict] = []
    grupo_atual_por_edicao: dict[str, dict] = {}
    for job in jobs:
        chave = str(job["edicao_id"] or f"sem-edicao-{job['id']}")
        precisa_novo_grupo = (
            chave not in grupo_atual_por_edicao
            or job["etapa"] == "baixando PDF"
            or job["edicao_id"] is None
        )
        if precisa_novo_grupo:
            grupo = {
                "edicao_id": job["edicao_id"],
                "titulo": job["titulo"] or "Sem edição",
                "data_publicacao": job["data_publicacao"],
                "url": job["url"],
                "status": "concluido",
                "atualizado_em": job["atualizado_em"],
                "jobs": [],
            }
            grupos.append(grupo)
            grupo_atual_por_edicao[chave] = grupo
        else:
            grupo = grupo_atual_por_edicao[chave]

        grupo["jobs"].append(job)
        if job["status"] == "erro":
            grupo["status"] = "erro"
        elif job["status"] == "rodando" and grupo["status"] != "erro":
            grupo["status"] = "rodando"
        elif job["status"] == "aviso" and grupo["status"] not in {"erro", "rodando"}:
            grupo["status"] = "aviso"
        if job["atualizado_em"] > grupo["atualizado_em"]:
            grupo["atualizado_em"] = job["atualizado_em"]

    grupos = sorted(grupos, key=lambda grupo: grupo["atualizado_em"], reverse=True)

    return templates.TemplateResponse(
        "status.html",
        {"request": request, "resumo": resumo, "jobs": jobs, "grupos": grupos},
    )


@app.get("/edicoes/{edicao_id}/pdf")
def abrir_pdf(edicao_id: int, page: int | None = None) -> FileResponse:
    with _conn() as conn:
        edicao = _row_or_404(conn, "SELECT caminho_local FROM edicoes WHERE id = ?", (edicao_id,))
    path = Path(edicao["caminho_local"] or "")
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF local não encontrado")
    headers = {}
    if page and page > 0:
        headers["Content-Disposition"] = f'inline; filename="{path.name}"'
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        headers=headers,
    )


@app.get("/edicoes/{edicao_id}/texto", response_class=PlainTextResponse)
def abrir_texto(edicao_id: int) -> str:
    with _conn() as conn:
        edicao = _row_or_404(
            conn, "SELECT texto_extraido_path FROM edicoes WHERE id = ?", (edicao_id,)
        )
    path = Path(edicao["texto_extraido_path"] or "")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Texto extraído não encontrado")
    return path.read_text(encoding="utf-8", errors="ignore")


@app.get("/paginas/{edicao_id}/{pagina}.png")
def imagem_pagina(edicao_id: int, pagina: int) -> FileResponse:
    if pagina < 1:
        raise HTTPException(status_code=400, detail="Página inválida")

    with _conn() as conn:
        edicao = _row_or_404(conn, "SELECT caminho_local FROM edicoes WHERE id = ?", (edicao_id,))

    pdf_path = Path(edicao["caminho_local"] or "")
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF local não encontrado")

    cache_dir = SETTINGS.download_dir / "_page_cache" / str(edicao_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    image_path = cache_dir / f"pagina-{pagina}.png"

    if not image_path.exists():
        imagens = convert_from_path(
            str(pdf_path),
            dpi=220,
            first_page=pagina,
            last_page=pagina,
        )
        if not imagens:
            raise HTTPException(status_code=404, detail="Página não encontrada")
        imagens[0].save(image_path, "PNG")

    return FileResponse(image_path, media_type="image/png", filename=image_path.name)


@app.get("/api/publicacoes")
def api_publicacoes() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT p.*, e.titulo AS edicao_titulo, e.data_publicacao, e.url
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            ORDER BY e.data_publicacao DESC, p.id DESC
            LIMIT 200
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/status")
def api_status() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT j.*, e.data_publicacao, e.url
            FROM jobs j
            LEFT JOIN edicoes e ON e.id = j.edicao_id
            ORDER BY j.id DESC
            LIMIT 200
            """
        ).fetchall()
    return [dict(row) for row in rows]
