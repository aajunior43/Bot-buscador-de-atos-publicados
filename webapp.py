from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Form
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pdf2image import convert_from_path
from starlette.requests import Request

import database
from config import SETTINGS
from detector import detectar
from downloader import baixar_edicao
from exporter import exportar_csv, exportar_json
from ocr import extrair_texto_rapido_com_estruturado_candidato
from scraper import Edicao, coletar_edicoes


BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    resume_ids = database.pop_interrupted_edicao_ids()
    if resume_ids:
        logger.warning(
            "Startup: %s edição(ões) interrompida(s) serão reprocessada(s): %s",
            len(resume_ids),
            resume_ids,
        )
        _task_executor.submit(_analisar_edicoes_lote, resume_ids)
    _start_scheduler()
    yield

app = FastAPI(title="Monitor O Regional - Inajá", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
from detector import _sem_acentos
templates.env.globals["_sem_acentos"] = _sem_acentos

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Autenticação HTTP Basic para toda a interface. Ativa quando WEBAPP_USER e
# WEBAPP_PASSWORD estão configurados; caso contrário emite aviso no log.
from auth_middleware import BasicAuthMiddleware

app.add_middleware(BasicAuthMiddleware)
if SETTINGS.webapp_user and SETTINGS.webapp_password:
    logger.info("Autenticação da interface web ATIVADA (usuário: %s).", SETTINGS.webapp_user)
else:
    logger.warning(
        "Autenticação da interface web DESATIVADA — defina WEBAPP_USER e "
        "WEBAPP_PASSWORD no .env para proteger /admin e as rotas de ação."
    )

_detector_lock = threading.Lock()
_analise_lock = threading.Lock()
_scheduler_started = False
_task_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="heavy-task")


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




# ---------------------------------------------------------------------------
# Detecção de edições
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Análise de edição
# ---------------------------------------------------------------------------

def _analisar_edicao_internal(edicao_id: int) -> None:
    """Corpo da análise de uma edição sem gerenciamento de lock."""
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
        progress_step="download",
        progress_current=0,
        progress_total=100,
    )
    # Simple progress updates for download phase (baixar_edicao doesn't expose callback yet)
    def dl_progress(p):
        if isinstance(p, dict):
            database.update_job(download_job, "rodando", mensagem=p.get("msg", "Baixando..."), progress_current=p.get("current"), progress_total=p.get("total"), progress_step=p.get("step", "download"))
        else:
            database.update_job(download_job, "rodando", mensagem=str(p))
    download = baixar_edicao(edicao, on_progress=dl_progress)
    database.update_job(
        download_job,
        "concluido",
        mensagem=f"PDF salvo em {download.caminho}",
        edicao_id=download.edicao_id,
        progress_step="download",
        progress_current=100,
        progress_total=100,
    )

    ocr_job = database.start_job(
        "rodando OCR",
        titulo=edicao.titulo,
        edicao_id=download.edicao_id,
        mensagem="Iniciando OCR rápido + estruturado...",
        progress_step="ocr",
    )
    def on_progress(msg: str | dict):
        """Support both legacy string and structured dict from OCR."""
        if isinstance(msg, dict):
            pc = msg.get("current")
            pt = msg.get("total")
            step = msg.get("step", "ocr")
            raw_msg = msg.get("msg", str(msg))
            database.update_job(ocr_job, "rodando", mensagem=raw_msg, progress_current=pc, progress_total=pt, progress_step=step)
        else:
            # Parse page progress if present for structured update
            import re
            m = re.search(r"Página\s+(\d+)\s*/\s*(\d+)", msg)
            pc = int(m.group(1)) if m else None
            pt = int(m.group(2)) if m else None
            database.update_job(ocr_job, "rodando", mensagem=msg, progress_current=pc, progress_total=pt, progress_step="ocr")
    ocr = extrair_texto_rapido_com_estruturado_candidato(download.caminho, on_progress=on_progress)
    ocr_status = "aviso" if ocr.avisos else "concluido"
    ocr_mensagem = f"{len(ocr.paginas)} página(s), {len(ocr.texto_completo)} caracteres"
    if ocr.avisos:
        ocr_mensagem += " | " + "; ".join(ocr.avisos)
    database.update_job(ocr_job, ocr_status, mensagem=ocr_mensagem, progress_step="ocr")

    detectar_job = database.start_job(
        "detectando publicações",
        titulo=edicao.titulo,
        edicao_id=download.edicao_id,
        progress_step="detect",
        progress_current=0,
        progress_total=100,
    )
    database.update_job(detectar_job, "rodando", mensagem="Analisando páginas para menções...", progress_current=30, progress_total=100, progress_step="detect")
    resultado = detectar(download.edicao_id, edicao.titulo, ocr.paginas)
    database.update_job(detectar_job, "rodando", mensagem="Processando publicações...", progress_current=70, progress_total=100, progress_step="detect")
    database.insert_mencoes(download.edicao_id, resultado.mencoes_db)
    database.insert_publicacoes(download.edicao_id, resultado.publicacoes)
    database.salvar_arquivos_atos_locais(ocr.texto_path, resultado.publicacoes)
    database.update_ocr(download.edicao_id, ocr.texto_path, resultado.encontrado)
    database.update_job(
        detectar_job,
        "concluido",
        mensagem=(
            f"{len(resultado.publicacoes)} publicação(ões), "
            f"{len(resultado.mencoes_db)} menção(ões)"
        ),
        progress_step="detect",
        progress_current=100,
        progress_total=100,
    )
    if resultado.encontrado:
        from notifier import notificar
        notificar(resultado, edicao)


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
        _analisar_edicao_internal(edicao_id)
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


def _analisar_edicoes_lote(edicao_ids: list[int]) -> None:
    for edicao_id in edicao_ids:
        if not _analise_lock.acquire(blocking=False):
            for _ in range(30):
                if _analise_lock.acquire(blocking=False):
                    break
                time.sleep(10)
            else:
                database.log_job(
                    "analisando edição",
                    "erro",
                    edicao_id=edicao_id,
                    mensagem="Timeout ao aguardar liberação da fila de processamento",
                )
                continue

        try:
            _analisar_edicao_internal(edicao_id)
        except Exception as exc:
            logger.exception("Falha na chamada de lote para edição %s", edicao_id)
        finally:
            _analise_lock.release()

        time.sleep(3)



# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = Query("", description="Busca por título, data, órgão, tipo ou assunto"),
) -> HTMLResponse:
    termo = f"%{q.strip()}%"
    with _conn() as conn:
        stats_db = conn.execute(
            """
            SELECT
              COUNT(*) AS total_edicoes,
              SUM(CASE WHEN tem_inaja = 1 THEN 1 ELSE 0 END) AS edicoes_inaja,
              SUM(CASE WHEN ocr_processado = 0 THEN 1 ELSE 0 END) AS pendentes_ocr,
              (SELECT COUNT(*) FROM publicacoes) AS total_publicacoes,
              (SELECT COUNT(*) FROM mencoes) AS total_mencoes
            FROM edicoes
            """
        ).fetchone()
        
        # Consolida valor financeiro total sob monitoramento
        rows_valores = conn.execute("SELECT valor FROM publicacoes WHERE valor IS NOT NULL AND valor != ''").fetchall()
        total_acumulado = 0.0
        import re
        for r in rows_valores:
            # Extract numeric part more robustly (handles R$ 1.234.567,89 or variants)
            match = re.search(r"[\d\.,]+", r["valor"] or "")
            if match:
                val_str = match.group(0).replace(".", "").replace(",", ".")
                try:
                    total_acumulado += float(val_str)
                except ValueError:
                    pass
        
        # Converte para formato legível (Ex: R$ 12,4M ou R$ 450.230,00)
        if total_acumulado >= 1_000_000.0:
            valor_formatado = f"R$ {total_acumulado / 1_000_000.0:.2f}M"
        elif total_acumulado > 0:
            valor_formatado = f"R$ {total_acumulado:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        else:
            valor_formatado = "R$ 0,00"
            
        stats = dict(stats_db)
        stats["total_valor_licitado"] = valor_formatado


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
                """,
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

    timeline = database.get_timeline_por_mes()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "edicoes": edicoes,
            "publicacoes": publicacoes,
            "atividade": atividade,
            "timeline": timeline,
            "q": q,
        },
    )


# ---------------------------------------------------------------------------
# Edições detectadas
# ---------------------------------------------------------------------------

@app.get("/edicoes-detectadas", response_class=HTMLResponse)
def edicoes_detectadas(
    request: Request,
    mes: str = Query("", description="Filtrar por mês YYYY-MM"),
) -> HTMLResponse:
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

        if mes.strip():
            edicoes = conn.execute(
                """
                SELECT e.*,
                  (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) AS publicacoes_count,
                  (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) AS mencoes_count,
                  (SELECT j.status FROM jobs j WHERE j.edicao_id = e.id ORDER BY j.id DESC LIMIT 1) AS ultimo_status,
                  (SELECT j.etapa  FROM jobs j WHERE j.edicao_id = e.id ORDER BY j.id DESC LIMIT 1) AS ultima_etapa
                FROM edicoes e
                WHERE substr(e.data_publicacao, 1, 7) = ?
                ORDER BY e.data_publicacao DESC, e.id DESC
                """,
                (mes.strip(),),
            ).fetchall()
        else:
            edicoes = conn.execute(
                """
                SELECT e.*,
                  (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id = e.id) AS publicacoes_count,
                  (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id = e.id) AS mencoes_count,
                  (SELECT j.status FROM jobs j WHERE j.edicao_id = e.id ORDER BY j.id DESC LIMIT 1) AS ultimo_status,
                  (SELECT j.etapa  FROM jobs j WHERE j.edicao_id = e.id ORDER BY j.id DESC LIMIT 1) AS ultima_etapa
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

    # Agrupa edições por mês (YYYY-MM) para a visualização
    meses_nomes = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
        5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
        9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
    }
    grupos_mes: list[dict] = []
    indice: dict[str, int] = {}
    for ed in edicoes:
        chave = (ed["data_publicacao"] or "")[:7]
        if not chave:
            chave = "sem-data"
        if chave not in indice:
            indice[chave] = len(grupos_mes)
            rotulo = "Sem data"
            if chave != "sem-data":
                try:
                    ano, mes = chave.split("-")
                    rotulo = f"{meses_nomes.get(int(mes), mes)} de {ano}"
                except (ValueError, KeyError):
                    rotulo = chave
            grupos_mes.append({"chave": chave, "rotulo": rotulo, "edicoes": []})
        grupos_mes[indice[chave]]["edicoes"].append(ed)

    return templates.TemplateResponse(
        request,
        "edicoes_detectadas.html",
        {
            "stats": stats,
            "edicoes": edicoes,
            "grupos_mes": grupos_mes,
            "ultimo_detector": ultimo_detector,
            "mes_filtro": mes.strip(),
        },
    )


@app.head("/edicoes-detectadas")
def edicoes_detectadas_head() -> Response:
    return Response(status_code=200)


@app.post("/edicoes-detectadas/detectar")
def detectar_edicoes_agora(background_tasks: BackgroundTasks, request: Request, format: str = Query("redirect")):
    _task_executor.submit(_detectar_edicoes_com_trava)
    if format == "json" or request.headers.get("accept", "").startswith("application/json"):
        return {"status": "started", "tipo": "detectar edições"}
    return RedirectResponse("/edicoes-detectadas", status_code=303)


@app.post("/edicoes/{edicao_id}/analisar")
def analisar_edicao_manual(
    edicao_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    format: str = Query("redirect", description="Use 'json' for AJAX response"),
):
    """Supports redirect or JSON based on format param or Accept header."""
    with _conn() as conn:
        _row_or_404(conn, "SELECT id FROM edicoes WHERE id = ?", (edicao_id,))
    _task_executor.submit(_analisar_edicao, edicao_id)
    if format == "json" or request.headers.get("accept", "").startswith("application/json"):
        return {"status": "started", "edicao_id": edicao_id, "job_etapa": "analisando edição"}
    return RedirectResponse("/edicoes-detectadas", status_code=303)


@app.post("/edicoes-detectadas/analisar-lote")
def analisar_lote_pendentes(limite: int = Form(5), request: Request = None, format: str = Query("redirect")):
    limite = max(1, min(limite, 50))
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id FROM edicoes
            WHERE ocr_processado = 0
              AND url NOT LIKE '%hnetsistemas.com.br%'
            ORDER BY data_publicacao DESC, id DESC
            LIMIT ?
            """,
            (limite,),
        ).fetchall()
    edicao_ids = [row["id"] for row in rows]
    if edicao_ids:
        _task_executor.submit(_analisar_edicoes_lote, edicao_ids)
    if format == "json" or (request and request.headers.get("accept", "").startswith("application/json")):
        return {"status": "started", "tipo": "lote", "count": len(edicao_ids)}
    return RedirectResponse("/edicoes-detectadas", status_code=303)



# ---------------------------------------------------------------------------
# Detalhe de edição
# ---------------------------------------------------------------------------

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
        # Número total de páginas (para o modo debug)
        num_paginas = 0
        if edicao["caminho_local"] and Path(edicao["caminho_local"]).exists():
            try:
                import pdfplumber
                with pdfplumber.open(edicao["caminho_local"]) as pdf:
                    num_paginas = len(pdf.pages)
            except Exception:
                logger.debug("Não foi possível abrir PDF para contar páginas: %s", edicao["caminho_local"])

    return templates.TemplateResponse(
        request,
        "edicao.html",
        {
            "edicao": edicao,
            "publicacoes": publicacoes,
            "mencoes": mencoes,
            "num_paginas": num_paginas,
        },
    )


@app.head("/edicoes/{edicao_id}")
def edicao_detail_head(edicao_id: int) -> Response:
    with _conn() as conn:
        _row_or_404(conn, "SELECT id FROM edicoes WHERE id = ?", (edicao_id,))
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

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
            FROM (
                SELECT * FROM jobs ORDER BY id DESC LIMIT 200
            ) j
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
        request,
        "status.html",
        {"resumo": resumo, "jobs": jobs, "grupos": grupos},
    )


@app.post("/status/limpar")
def limpar_status_jobs() -> RedirectResponse:
    database.clear_jobs_history()
    return RedirectResponse("/status", status_code=303)



# ---------------------------------------------------------------------------
# PDF e imagens
# ---------------------------------------------------------------------------

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
            poppler_path=SETTINGS.poppler_path or None,
        )
        if not imagens:
            raise HTTPException(status_code=404, detail="Página não encontrada")
        imagens[0].save(image_path, "PNG")

    return FileResponse(image_path, media_type="image/png", filename=image_path.name)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, msg: str = "") -> HTMLResponse:
    api_key = database.get_setting("opencode_api_key", "") or SETTINGS.opencode_api_key
    model = database.get_setting("opencode_model", "") or SETTINGS.opencode_model
    api_key_masked = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else ("***" if api_key else "")

    extra_terms = database.get_setting("extra_terms", "") or ",".join(SETTINGS.extra_terms)
    ignore_terms = database.get_setting("ignore_terms", "") or ",".join(SETTINGS.ignore_context_terms)
    smtp_host = database.get_setting("smtp_host", "") or SETTINGS.smtp_host
    smtp_port = database.get_setting("smtp_port", "") or str(SETTINGS.smtp_port)
    smtp_user = database.get_setting("smtp_user", "") or SETTINGS.smtp_user
    smtp_to = database.get_setting("smtp_to", "") or SETTINGS.smtp_to
    webhook_url = database.get_setting("webhook_url", "") or SETTINGS.webhook_url

    webhooks = database.get_webhooks()

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "api_key_configured": bool(api_key),
            "api_key_masked": api_key_masked,
            "model": model or "deepseek-v4-flash",
            "ai_ativo": bool(api_key) and SETTINGS.ai_refine_publications,
            "extra_terms": extra_terms,
            "ignore_terms": ignore_terms,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "smtp_to": smtp_to,
            "webhook_url": webhook_url,
            "webhooks": webhooks,
            "msg": msg,
            "teste_resultado": None,
        },
    )


@app.post("/admin/limpar-edicoes-antigas")
def limpar_edicoes_antigas() -> RedirectResponse:
    database.delete_hnetsistemas_edicoes()
    return RedirectResponse("/admin?msg=Edições de sistema antigo removidas com sucesso!", status_code=303)


@app.post("/admin/salvar")

async def admin_salvar(request: Request) -> RedirectResponse:
    form = await request.form()
    fields = {
        "opencode_api_key": str(form.get("opencode_api_key", "")),
        "opencode_model": str(form.get("opencode_model", "")),
        "extra_terms": str(form.get("extra_terms", "")),
        "ignore_terms": str(form.get("ignore_terms", "")),
        "smtp_host": str(form.get("smtp_host", "")),
        "smtp_port": str(form.get("smtp_port", "")),
        "smtp_user": str(form.get("smtp_user", "")),
        "smtp_pass": str(form.get("smtp_pass", "")),
        "smtp_to": str(form.get("smtp_to", "")),
        "smtp_from": str(form.get("smtp_from", "")),
        "webhook_url": str(form.get("webhook_url", "")),
        "absence_alert_days": str(form.get("absence_alert_days", "30")),
    }
    for chave, valor in fields.items():
        if valor.strip():
            database.set_setting(chave, valor.strip())
    return RedirectResponse("/admin?msg=Configurações salvas com sucesso!", status_code=303)


@app.post("/admin/testar")
def admin_testar(request: Request) -> HTMLResponse:
    from ai_processor import _api_key, _extrair_publicacao
    key = _api_key()
    if not key:
        resultado = "❌ Nenhuma API Key configurada."
    else:
        try:
            r = _extrair_publicacao("DECRETO Nº 001/2026 - Prefeitura Municipal de Inajá - PR.", timeout=15)
            resultado = f"✅ OK — Resposta recebida: {str(r)[:200]}"
        except Exception as exc:
            resultado = f"❌ Erro: {exc}"

    api_key = database.get_setting("opencode_api_key", "") or SETTINGS.opencode_api_key
    model = database.get_setting("opencode_model", "") or SETTINGS.opencode_model
    api_key_masked = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else ("***" if api_key else "")
    webhooks = database.get_webhooks()
    extra_terms = database.get_setting("extra_terms", "") or ",".join(SETTINGS.extra_terms)
    ignore_terms = database.get_setting("ignore_terms", "") or ",".join(SETTINGS.ignore_context_terms)
    smtp_host = database.get_setting("smtp_host", "") or SETTINGS.smtp_host
    smtp_port = database.get_setting("smtp_port", "") or str(SETTINGS.smtp_port)
    smtp_user = database.get_setting("smtp_user", "") or SETTINGS.smtp_user
    smtp_to = database.get_setting("smtp_to", "") or SETTINGS.smtp_to
    webhook_url = database.get_setting("webhook_url", "") or SETTINGS.webhook_url

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "api_key_configured": bool(api_key),
            "api_key_masked": api_key_masked,
            "model": model or "deepseek-v4-flash",
            "ai_ativo": bool(api_key) and SETTINGS.ai_refine_publications,
            "extra_terms": extra_terms,
            "ignore_terms": ignore_terms,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "smtp_to": smtp_to,
            "webhook_url": webhook_url,
            "webhooks": webhooks,
            "msg": "",
            "teste_resultado": resultado,
        },
    )


@app.post("/admin/webhook/adicionar")
async def admin_webhook_adicionar(request: Request) -> RedirectResponse:
    form = await request.form()
    url = str(form.get("webhook_url", "")).strip()
    descricao = str(form.get("webhook_descricao", "")).strip()
    if url:
        database.upsert_webhook(url, descricao)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/webhook/{webhook_id}/remover")
def admin_webhook_remover(webhook_id: int) -> RedirectResponse:
    database.delete_webhook(webhook_id)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Notificações
# ---------------------------------------------------------------------------

@app.get("/notificacoes", response_class=HTMLResponse)
def notificacoes_page(request: Request) -> HTMLResponse:
    notificacoes = database.get_notificacoes(limit=200)
    return templates.TemplateResponse(
        request,
        "notificacoes.html",
        {"notificacoes": notificacoes},
    )


# ---------------------------------------------------------------------------
# Exportar
# ---------------------------------------------------------------------------

@app.get("/exportar", response_class=HTMLResponse)
def exportar_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "exportar.html", {})


@app.get("/api/exportar")
def api_exportar(
    format: str = Query("json", description="csv ou json"),
    data_inicio: str = Query("", description="YYYY-MM-DD"),
    data_fim: str = Query("", description="YYYY-MM-DD"),
    tipo: str = Query("", description="Filtrar por tipo de ato"),
    orgao: str = Query("", description="Filtrar por órgão"),
    apenas_inaja: bool = Query(False, description="Apenas edições com Inajá"),
) -> Response:
    di = data_inicio.strip() or None
    df = data_fim.strip() or None
    tp = tipo.strip() or None
    org = orgao.strip() or None

    if format.lower() == "csv":
        conteudo = exportar_csv(di, df, tp, org, apenas_inaja)
        return Response(
            content=conteudo,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=publicacoes.csv"},
        )
    dados = exportar_json(di, df, tp, org, apenas_inaja)
    return JSONResponse(content=dados)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

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


def _live_status_for_edicao(edicao_id: int) -> dict:
    """Return recent jobs + live state for a specific edition (for UI live monitor)."""
    with _conn() as conn:
        jobs = conn.execute(
            """
            SELECT * FROM jobs 
            WHERE edicao_id = ? 
            ORDER BY id DESC 
            LIMIT 20
            """,
            (edicao_id,),
        ).fetchall()
        running = [dict(j) for j in jobs if j["status"] == "rodando"]
        return {
            "edicao_id": edicao_id,
            "jobs": [dict(j) for j in jobs],
            "has_running": bool(running),
            "current": running[0] if running else None,
        }


@app.get("/api/edicoes/{edicao_id}/live-status")
def api_edicao_live_status(edicao_id: int) -> dict:
    return _live_status_for_edicao(edicao_id)


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


@app.get("/api/graficos/por-mes")
def api_graficos_por_mes() -> list[dict]:
    """Dados para gráfico de linha do tempo — publicações por mês."""
    return database.get_publicacoes_por_mes()


@app.get("/api/graficos/por-tipo")
def api_graficos_por_tipo() -> list[dict]:
    """Dados para gráfico de pizza — publicações por tipo de ato."""
    return database.get_publicacoes_por_tipo()


@app.get("/api/eventos")
async def api_eventos(request: Request) -> StreamingResponse:
    """Server-Sent Events para auto-refresh do dashboard sem polling."""
    async def gerador():
        while True:
            if await request.is_disconnected():
                break
            with _conn() as conn:
                atividade = _atividade_atual(conn)
            data = json.dumps(atividade)
            yield f"data: {data}\n\n"
            await asyncio.sleep(4)

    return StreamingResponse(
        gerador(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Refinamento IA manual
# ---------------------------------------------------------------------------

@app.post("/edicoes/{edicao_id}/ia")
def refinar_ia_manual(edicao_id: int, request: Request = None, format: str = Query("redirect")):
    with _conn() as conn:
        row = _row_or_404(conn, "SELECT texto_extraido_path FROM edicoes WHERE id = ?", (edicao_id,))
        texto_path = row["texto_extraido_path"]
        publicacoes_rows = conn.execute(
            "SELECT * FROM publicacoes WHERE edicao_id = ?", (edicao_id,)
        ).fetchall()

    if not publicacoes_rows:
        if format == "json" or (request and request.headers.get("accept", "").startswith("application/json")):
            return {"status": "no_publicacoes", "edicao_id": edicao_id}
        return RedirectResponse(f"/edicoes/{edicao_id}", status_code=303)

    ia_job = database.start_job(
        "refinando com IA",
        titulo=None,
        edicao_id=edicao_id,
        mensagem="Iniciando refinamento IA...",
        progress_step="ia",
    )

    def _refinar():
        from ai_processor import refinar_publicacoes
        pubs = [dict(r) for r in publicacoes_rows]
        database.update_job(ia_job, "rodando", mensagem="Refinando publicações com IA...", progress_current=10, progress_total=100, progress_step="ia")
        refinadas = refinar_publicacoes(pubs)
        database.insert_publicacoes(edicao_id, refinadas)
        if texto_path:
            database.salvar_arquivos_atos_locais(texto_path, refinadas)
        database.update_job(ia_job, "concluido", mensagem="Refinamento IA concluído", progress_current=100, progress_total=100, progress_step="ia")

    _task_executor.submit(_refinar)
    if format == "json" or (request and request.headers.get("accept", "").startswith("application/json")):
        return {"status": "started", "edicao_id": edicao_id, "job_etapa": "refinando com IA"}
    return RedirectResponse(f"/edicoes/{edicao_id}", status_code=303)

