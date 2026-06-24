from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pdf2image import convert_from_path
from starlette.requests import Request

import database
from config import SETTINGS
from detector import detectar
from downloader import baixar_edicao
from ocr_processor import extrair_texto_rapido_com_estruturado_candidato
from scraper import Edicao, coletar_edicoes


BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
app = FastAPI(title="Monitor O Regional - Inajá")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
_detector_lock = threading.Lock()
_analise_lock = threading.Lock()
_scheduler_started = False


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_or_404(conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Row:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Registro não encontrado")
    return row


def _atividade_atual(conn: sqlite3.Connection) -> dict:
    rodando = conn.execute(
        """
        SELECT j.*, e.titulo AS edicao_titulo, e.data_publicacao
        FROM jobs j
        LEFT JOIN edicoes e ON e.id = j.edicao_id
        WHERE j.status = 'rodando'
        ORDER BY j.id DESC
        LIMIT 5
        """
    ).fetchall()
    recentes = conn.execute(
        """
        SELECT j.*, e.titulo AS edicao_titulo, e.data_publicacao
        FROM jobs j
        LEFT JOIN edicoes e ON e.id = j.edicao_id
        ORDER BY j.id DESC
        LIMIT 8
        """
    ).fetchall()
    return {
        "rodando": [dict(item) for item in rodando],
        "recentes": [dict(item) for item in recentes],
        "tem_atividade": bool(rodando),
    }


@app.on_event("startup")
def startup() -> None:
    database.init_db()
    _start_scheduler()


def _registrar_edicoes_detectadas() -> int:
    job_id = database.start_job(
        "detectando edições",
        mensagem=f"Varredura automática em {SETTINGS.site_url}",
    )
    try:
        edicoes = coletar_edicoes()
        for edicao in edicoes:
            database.insert_or_get_edicao(
                edicao.url,
                edicao.titulo,
                edicao.data_publicacao,
            )
        database.update_job(
            job_id,
            "concluido",
            mensagem=f"{len(edicoes)} edição(ões) detectada(s)",
        )
        return len(edicoes)
    except Exception as exc:
        logger.exception("Falha ao detectar edições.")
        database.update_job(job_id, "erro", mensagem=str(exc))
        return 0


def _detectar_edicoes_com_trava() -> None:
    if not _detector_lock.acquire(blocking=False):
        database.log_job(
            "detectando edições",
            "ignorado",
            mensagem="Varredura já em execução",
        )
        return
    try:
        _registrar_edicoes_detectadas()
    finally:
        _detector_lock.release()


def _scheduler_loop() -> None:
    time.sleep(2)
    _detectar_edicoes_com_trava()
    intervalo = max(1, int(24 / 4))
    while True:
        time.sleep(intervalo * 60 * 60)
        _detectar_edicoes_com_trava()


def _start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    thread = threading.Thread(
        target=_scheduler_loop,
        name="edicoes-detector-4x-dia",
        daemon=True,
    )
    thread.start()


def _analisar_edicao(edicao_id: int) -> None:
    if not _analise_lock.acquire(blocking=False):
        database.log_job(
            "analisando edição",
            "ignorado",
            edicao_id=edicao_id,
            mensagem="Outra análise já está em execução",
        )
        return
    try:
        with _conn() as conn:
            row = _row_or_404(conn, "SELECT * FROM edicoes WHERE id = ?", (edicao_id,))
            edicao = Edicao(
                url=row["url"],
                titulo=row["titulo"] or f"Edição {row['id']}",
                data_publicacao=row["data_publicacao"],
            )

        download_job = database.start_job(
            "baixando PDF",
            titulo=edicao.titulo,
            edicao_id=edicao_id,
            mensagem=edicao.url,
        )
        download = baixar_edicao(edicao)
        database.update_job(
            download_job,
            "concluido",
            mensagem=f"PDF salvo em {download.caminho}",
            edicao_id=download.edicao_id,
        )

        ocr_job = database.start_job(
            "rodando OCR",
            titulo=edicao.titulo,
            edicao_id=download.edicao_id,
            mensagem="OCR rápido + estruturado em páginas candidatas",
        )
        ocr = extrair_texto_rapido_com_estruturado_candidato(download.caminho)
        ocr_status = "aviso" if ocr.avisos else "concluido"
        ocr_mensagem = f"{len(ocr.paginas)} página(s), {len(ocr.texto_completo)} caracteres"
        if ocr.avisos:
            ocr_mensagem += " | " + "; ".join(ocr.avisos)
        database.update_job(ocr_job, ocr_status, mensagem=ocr_mensagem)

        detectar_job = database.start_job(
            "detectando publicações",
            titulo=edicao.titulo,
            edicao_id=download.edicao_id,
        )
        resultado = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
        database.insert_mencoes(download.edicao_id, resultado.mencoes_db)
        database.insert_publicacoes(download.edicao_id, resultado.publicacoes)
        database.update_ocr(download.edicao_id, ocr.texto_path, resultado.encontrado)
        database.update_job(
            detectar_job,
            "concluido",
            mensagem=(
                f"{len(resultado.publicacoes)} publicação(ões), "
                f"{len(resultado.mencoes_db)} menção(ões)"
            ),
        )
    except Exception as exc:
        logger.exception("Falha ao analisar edição %s.", edicao_id)
        database.log_job(
            "analisando edição",
            "erro",
            edicao_id=edicao_id,
            mensagem=str(exc),
        )
    finally:
        _analise_lock.release()


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
        atividade = _atividade_atual(conn)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "edicoes": edicoes,
            "publicacoes": publicacoes,
            "atividade": atividade,
            "q": q,
        },
    )


@app.get("/edicoes-detectadas", response_class=HTMLResponse)
def edicoes_detectadas(request: Request) -> HTMLResponse:
    with _conn() as conn:
        stats = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN ocr_processado = 1 THEN 1 ELSE 0 END) AS analisadas,
              SUM(CASE WHEN ocr_processado = 0 THEN 1 ELSE 0 END) AS pendentes,
              SUM(CASE WHEN tem_inaja = 1 THEN 1 ELSE 0 END) AS com_inaja
            FROM edicoes
            """
        ).fetchone()
        edicoes = conn.execute(
            """
            SELECT e.*,
              (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) AS publicacoes_count,
              (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) AS mencoes_count,
              (
                SELECT j.status
                FROM jobs j
                WHERE j.edicao_id = e.id
                ORDER BY j.id DESC
                LIMIT 1
              ) AS ultimo_status,
              (
                SELECT j.etapa
                FROM jobs j
                WHERE j.edicao_id = e.id
                ORDER BY j.id DESC
                LIMIT 1
              ) AS ultima_etapa
            FROM edicoes e
            ORDER BY e.data_publicacao DESC, e.id DESC
            """
        ).fetchall()
        ultimo_detector = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE etapa = 'detectando edições'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    return templates.TemplateResponse(
        "edicoes_detectadas.html",
        {
            "request": request,
            "stats": stats,
            "edicoes": edicoes,
            "ultimo_detector": ultimo_detector,
        },
    )


@app.head("/edicoes-detectadas")
def edicoes_detectadas_head() -> Response:
    return Response(status_code=200)


@app.post("/edicoes-detectadas/detectar")
def detectar_edicoes_agora(background_tasks: BackgroundTasks) -> RedirectResponse:
    background_tasks.add_task(_detectar_edicoes_com_trava)
    return RedirectResponse("/edicoes-detectadas", status_code=303)


@app.post("/edicoes/{edicao_id}/analisar")
def analisar_edicao_manual(
    edicao_id: int,
    background_tasks: BackgroundTasks,
) -> RedirectResponse:
    with _conn() as conn:
        _row_or_404(conn, "SELECT id FROM edicoes WHERE id = ?", (edicao_id,))
    background_tasks.add_task(_analisar_edicao, edicao_id)
    return RedirectResponse("/edicoes-detectadas", status_code=303)


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


@app.head("/edicoes/{edicao_id}")
def edicao_detail_head(edicao_id: int) -> Response:
    with _conn() as conn:
        _row_or_404(conn, "SELECT id FROM edicoes WHERE id = ?", (edicao_id,))
    return Response(status_code=200)


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


@app.get("/api/atividade")
def api_atividade() -> dict:
    with _conn() as conn:
        return _atividade_atual(conn)


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
