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
from exporter import exportar_csv, exportar_json
from pipeline import processar_edicao_por_id
from scraper import coletar_edicoes


BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


def _auth_obrigatoria() -> bool:
    return bool(SETTINGS.require_webapp_auth or SETTINGS.app_env == "production")


def _validar_auth_startup() -> None:
    """Em produção / com REQUIRE_WEBAPP_AUTH, exige credenciais antes de servir."""
    tem_creds = bool(SETTINGS.webapp_user and SETTINGS.webapp_password)
    if _auth_obrigatoria() and not tem_creds:
        raise RuntimeError(
            "Autenticação da interface web é obrigatória "
            f"(APP_ENV={SETTINGS.app_env!r}, REQUIRE_WEBAPP_AUTH={SETTINGS.require_webapp_auth}). "
            "Defina WEBAPP_USER e WEBAPP_PASSWORD no .env, ou use APP_ENV=development "
            "apenas em ambientes locais."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validar_auth_startup()
    database.init_db()
    # Limpa jobs "rodando" de crash anterior. OCR de pendentes fica com o BOT.
    resume_ids = database.pop_interrupted_edicao_ids()
    if resume_ids:
        logger.warning(
            "Startup WEB: %s job(s) interrompido(s) limpo(s); "
            "reprocessamento OCR fica a cargo do BOT: %s",
            len(resume_ids),
            resume_ids,
        )
    stuck = database.cleanup_stuck_jobs(max_hours=2)
    if stuck:
        logger.warning("Startup WEB: %s job(s) travado(s) marcado(s) como erro", stuck)
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
elif _auth_obrigatoria():
    logger.error(
        "Auth obrigatória configurada, mas credenciais ausentes — o startup deve falhar."
    )
else:
    logger.warning(
        "Autenticação da interface web DESATIVADA — defina WEBAPP_USER e "
        "WEBAPP_PASSWORD no .env (ou APP_ENV=production / REQUIRE_WEBAPP_AUTH=true) "
        "para proteger /admin e as rotas de ação."
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
    """Só cadastra edições no banco — OCR fica a cargo do BOT (main.py)."""
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
        msg = f"{len(edicoes)} edição(ões) detectada(s)"
        database.update_job(job_id, "concluido", mensagem=msg)
        database.registrar_evento_ciclo("web_scan", msg)
        return len(edicoes)
    except Exception as exc:
        logger.exception("Falha ao detectar edições.")
        database.update_job(job_id, "erro", mensagem=str(exc))
        database.registrar_evento_ciclo("web_scan", f"erro: {exc}")
        return 0


def _detectar_edicoes_com_trava() -> None:
    """Varredura WEB: apenas lista/cadastra URLs. Não roda OCR."""
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
    # Aguarda web subir, depois 1ª varredura (cadastro apenas)
    time.sleep(3)
    _detectar_edicoes_com_trava()
    intervalo_h = max(1, int(SETTINGS.web_scan_interval_hours or 6))
    while True:
        time.sleep(intervalo_h * 60 * 60)
        _detectar_edicoes_com_trava()


def _start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    thread = threading.Thread(
        target=_scheduler_loop,
        name="auto-scan-web",
        daemon=True,
    )
    thread.start()
    logger.info(
        "WEB em modo varredura: cadastra edições a cada %sh "
        "(OCR/notificações ficam no BOT; AUTO_PROCESS=%s, limite=%s, dias=%s).",
        SETTINGS.web_scan_interval_hours,
        SETTINGS.auto_process,
        SETTINGS.auto_process_limit,
        SETTINGS.auto_process_dias,
    )


# ---------------------------------------------------------------------------
# Análise de edição (delega ao pipeline unificado)
# ---------------------------------------------------------------------------

def _analisar_edicao_internal(edicao_id: int) -> None:
    """Corpo da análise de uma edição sem gerenciamento de lock."""
    # force_ocr + fast_ocr = OCR rápido + estruturado em páginas candidatas
    # (mesmo comportamento histórico da webapp). Progresso detalhado fica no pipeline.
    processar_edicao_por_id(
        edicao_id,
        force_ocr=True,
        fast_ocr=True,
        notificar_se_encontrado=True,
    )


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
# Dashboard (Leitura — Atos)
# ---------------------------------------------------------------------------

def _stats_basicos(conn: sqlite3.Connection) -> dict:
    stats_db = conn.execute(
        """
        SELECT
          COUNT(*) AS total_edicoes,
          COALESCE(SUM(CASE WHEN tem_inaja = 1 THEN 1 ELSE 0 END), 0) AS edicoes_inaja,
          COALESCE(SUM(CASE WHEN ocr_processado = 0 THEN 1 ELSE 0 END), 0) AS pendentes_ocr,
          (SELECT COUNT(*) FROM publicacoes) AS total_publicacoes,
          (SELECT COUNT(*) FROM mencoes) AS total_mencoes
        FROM edicoes
        """
    ).fetchone()
    fin = database.somar_valores_publicacoes(deduplicar=True)
    ultima = conn.execute(
        """
        SELECT e.data_publicacao
        FROM publicacoes p
        JOIN edicoes e ON e.id = p.edicao_id
        WHERE e.data_publicacao IS NOT NULL AND e.data_publicacao != ''
        ORDER BY e.data_publicacao DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "total_edicoes": int(stats_db["total_edicoes"] or 0),
        "edicoes_inaja": int(stats_db["edicoes_inaja"] or 0),
        "pendentes_ocr": int(stats_db["pendentes_ocr"] or 0),
        "total_publicacoes": int(stats_db["total_publicacoes"] or 0),
        "total_mencoes": int(stats_db["total_mencoes"] or 0),
        "total_valor_licitado": database.formatar_reais(float(fin["total"])),
        "valor_n_unicos": int(fin["n_unicos"]),
        "valor_n_brutos": int(fin["n_com_valor"]),
        "ultima_deteccao": (ultima["data_publicacao"] if ultima else None) or "—",
    }


def _saude_sistema() -> dict:
    from ai_processor import _api_key, _auth_bloqueada, ia_disponivel

    key = _api_key()
    auto = database.get_status_automacao()
    return {
        "telegram_ok": False,
        "ai_key": bool(key),
        "ai_refine": bool(SETTINGS.ai_refine_publications),
        "ai_auth_ok": bool(key) and not _auth_bloqueada,
        "ai_disponivel": ia_disponivel(),
        "web_auth": bool(SETTINGS.webapp_user and SETTINGS.webapp_password),
        "app_env": SETTINGS.app_env or "development",
        "auto_process": bool(SETTINGS.auto_process),
        "auto_process_limit": int(SETTINGS.auto_process_limit),
        "auto_process_max_por_ciclo": int(SETTINGS.auto_process_max_por_ciclo),
        "auto_process_continuo": bool(SETTINGS.auto_process_continuo),
        "auto_process_dias": int(SETTINGS.auto_process_dias),
        "auto_process_desde": (SETTINGS.auto_process_desde or "").strip(),
        "bot_vivo": bool(auto.get("bot_vivo")),
        "bot_heartbeat_rel": auto.get("bot_heartbeat_rel") or "sem sinal",
        "bot_proxima_rel": auto.get("bot_proxima_rel") or "—",
        "pendentes_ocr": int(auto.get("pendentes_ocr") or 0),
        "fila_proximo_ciclo": int(auto.get("fila_proximo_ciclo") or 0),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = Query("", description="Busca por título, data, órgão, tipo ou assunto"),
    mes: str = Query("", description="Filtro mês YYYY-MM"),
    tipo: str = Query("", description="Filtro tipo de ato (ex.: Decreto)"),
    modo: str = Query("", description="semantico = ranking inteligente"),
) -> HTMLResponse:
    """Home de leitura: busca + KPIs leigos + lista de atos."""
    termo = f"%{q.strip()}%"
    mes_f = mes.strip()
    tipo_f = tipo.strip()
    modo_sem = modo.strip().casefold() in {"semantico", "smart", "1", "ia"}
    with _conn() as conn:
        stats = _stats_basicos(conn)
        filtros = []
        params: list[object] = []
        if q.strip() and not modo_sem:
            filtros.append(
                "(e.titulo LIKE ? OR e.data_publicacao LIKE ? OR p.orgao LIKE ? "
                "OR p.tipo LIKE ? OR p.assunto LIKE ? OR p.resumo_ia LIKE ? OR p.numero LIKE ?)"
            )
            params.extend([termo, termo, termo, termo, termo, termo, termo])
        if mes_f and len(mes_f) >= 7:
            filtros.append("e.data_publicacao LIKE ?")
            params.append(f"{mes_f[:7]}%")
        if tipo_f:
            filtros.append("LOWER(COALESCE(p.tipo, '')) = LOWER(?)")
            params.append(tipo_f)
        where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
        publicacoes = conn.execute(
            f"""
            SELECT p.*, e.titulo AS edicao_titulo, e.data_publicacao
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            {where}
            ORDER BY e.data_publicacao DESC, p.id DESC
            LIMIT {"400" if modo_sem and q.strip() else "50"}
            """,
            params,
        ).fetchall()
        if modo_sem and q.strip():
            from inteligencia import rankear_publicacoes

            publicacoes = rankear_publicacoes(
                q.strip(), [dict(r) for r in publicacoes], limit=50
            )
        meses = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT substr(e.data_publicacao, 1, 7) AS mes
                FROM publicacoes p
                JOIN edicoes e ON e.id = p.edicao_id
                WHERE e.data_publicacao IS NOT NULL AND e.data_publicacao != ''
                ORDER BY mes DESC
                LIMIT 24
                """
            ).fetchall()
            if r[0]
        ]
        tipos = [
            r[0]
            for r in conn.execute(
                """
                SELECT tipo, COUNT(*) AS n
                FROM publicacoes
                WHERE tipo IS NOT NULL AND TRIM(tipo) != ''
                GROUP BY tipo
                ORDER BY n DESC, tipo ASC
                LIMIT 12
                """
            ).fetchall()
            if r[0]
        ]
        atividade = _atividade_atual(conn)
        so_mencao_pend = conn.execute(
            """
            SELECT COUNT(*) FROM edicoes e
            WHERE e.tem_inaja = 1
              AND NOT EXISTS (SELECT 1 FROM publicacoes p WHERE p.edicao_id = e.id)
              AND (e.revisao_so_mencao IS NULL OR e.revisao_so_mencao = ''
                   OR e.revisao_so_mencao = 'pendente')
            """
        ).fetchone()[0]

    saude = _saude_sistema()
    saude["so_mencao_pendentes"] = so_mencao_pend
    try:
        from agente import listar_log, status_agente

        agente_home = status_agente()
        agente_log = listar_log(5)
    except Exception:
        agente_home = {}
        agente_log = []

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "publicacoes": publicacoes,
            "atividade": atividade,
            "q": q,
            "mes": mes_f[:7] if mes_f else "",
            "tipo": tipo_f,
            "modo": "semantico" if modo_sem else "",
            "meses": meses,
            "tipos": tipos,
            "saude": saude,
            "resumo_diario": database.get_resumo_diario(),
            "agente_home": agente_home,
            "agente_log": agente_log,
        },
    )


@app.get("/atos", response_class=HTMLResponse)
def atos_alias(
    request: Request,
    q: str = Query("", description="Busca"),
    mes: str = Query("", description="Filtro mês"),
    tipo: str = Query("", description="Filtro tipo"),
    modo: str = Query("", description="semantico"),
) -> HTMLResponse:
    """Alias de leitura para a home de atos."""
    return dashboard(request, q=q, mes=mes, tipo=tipo, modo=modo)


@app.get("/operacao", response_class=HTMLResponse)
def operacao(
    request: Request,
    tab: str = Query("automacao", description="automacao | fila"),
) -> HTMLResponse:
    """Hub operacional: abas Automação + Fila (cockpit unificado)."""
    tab_n = (tab or "automacao").strip().casefold()
    if tab_n not in {"automacao", "fila"}:
        tab_n = "automacao"
    with _conn() as conn:
        stats = _stats_basicos(conn)
        atividade = _atividade_atual(conn)
        fila_resumo = {
            "rodando": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='rodando'"
            ).fetchone()[0],
            "erro": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='erro'"
            ).fetchone()[0],
            "concluido": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='concluido'"
            ).fetchone()[0],
            "aviso": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='aviso'"
            ).fetchone()[0],
        }
        jobs_recentes = [
            dict(r)
            for r in conn.execute(
                """
                SELECT j.id, j.etapa, j.status, j.mensagem, j.atualizado_em,
                       j.edicao_id, e.titulo AS edicao_titulo, e.data_publicacao
                FROM jobs j
                LEFT JOIN edicoes e ON e.id = j.edicao_id
                ORDER BY j.id DESC
                LIMIT 12
                """
            ).fetchall()
        ]
    metricas = database.get_metricas_qualidade()
    automacao = database.get_status_automacao()
    resumo = database.get_resumo_diario()
    fila_full = (
        _dados_fila_jobs()
        if tab_n == "fila"
        else {"resumo": None, "grupos": []}
    )
    resumo_fila = fila_full.get("resumo") or {
        "rodando": fila_resumo["rodando"],
        "avisos": fila_resumo["aviso"],
        "erros": fila_resumo["erro"],
        "concluidos": fila_resumo["concluido"],
        "total": 0,
    }
    return templates.TemplateResponse(
        request,
        "operacao.html",
        {
            "stats": stats,
            "atividade": atividade,
            "metricas": metricas,
            "saude": _saude_sistema(),
            "automacao": automacao,
            "resumo_diario": resumo,
            "fila_resumo": fila_resumo,
            "jobs_recentes": jobs_recentes,
            "tab": tab_n,
            "resumo": resumo_fila,
            "grupos": fila_full.get("grupos") or [],
        },
    )


@app.post("/operacao/resumo-diario")
def gerar_resumo_diario_agora() -> RedirectResponse:
    from inteligencia import gerar_resumo_diario_from_db

    database.init_db()
    gerar_resumo_diario_from_db()
    return RedirectResponse(url="/operacao", status_code=303)


@app.post("/publicacoes/{pub_id}/feedback")
def feedback_publicacao(
    pub_id: int,
    feedback: str = Form(""),
    next: str = Form("/"),
) -> RedirectResponse:
    """Feedback humano: correto | errado (treina confiança futura)."""
    database.init_db()
    database.set_feedback_publicacao(pub_id, feedback)
    dest = next.strip() or "/"
    if not dest.startswith("/"):
        dest = "/"
    return RedirectResponse(url=dest, status_code=303)


@app.post("/publicacoes/{pub_id}/explicar")
def explicar_publicacao(
    pub_id: int,
    next: str = Form("/"),
) -> RedirectResponse:
    """Gera explicação leiga sob demanda."""
    from ai_processor import gerar_explicacao_leiga

    database.init_db()
    pub = database.get_publicacao_by_id(pub_id)
    if pub and not pub.get("explicacao_ia"):
        exp = gerar_explicacao_leiga(pub)
        if exp:
            database.update_explicacao_publicacao(pub_id, exp)
    dest = next.strip() or "/"
    if not dest.startswith("/"):
        dest = "/"
    return RedirectResponse(url=dest, status_code=303)


@app.post("/publicacoes/{pub_id}/similares")
def pub_similares(pub_id: int, next: str = Form("")) -> RedirectResponse:
    pub = database.get_publicacao_by_id(pub_id)
    ed_id = pub.get("edicao_id") if pub else None
    if next.strip().startswith("/"):
        dest = next.strip()
    elif ed_id:
        dest = f"/edicoes/{ed_id}?painel=similares&pub={pub_id}"
    else:
        dest = "/"
    return RedirectResponse(url=dest, status_code=303)


@app.post("/publicacoes/{pub_id}/timeline")
def pub_timeline(pub_id: int, next: str = Form("")) -> RedirectResponse:
    pub = database.get_publicacao_by_id(pub_id)
    ed_id = pub.get("edicao_id") if pub else None
    if next.strip().startswith("/"):
        dest = next.strip()
    elif ed_id:
        dest = f"/edicoes/{ed_id}?painel=timeline&pub={pub_id}"
    else:
        dest = "/"
    return RedirectResponse(url=dest, status_code=303)


@app.get("/inteligencia", response_class=HTMLResponse)
def pagina_inteligencia(
    request: Request,
    desde: str = Query(""),
    ate: str = Query(""),
) -> HTMLResponse:
    """Painel #12 temas, #14 ranking, #13 LRF, #3 anomalias."""
    database.init_db()
    d = desde.strip() or None
    a = ate.strip() or None
    ranking = database.ranking_publicacoes(desde=d, ate=a, limit=12)
    temas = database.contar_temas(desde=d, ate=a, limit=24)
    lrf = database.listar_radar_lrf(limit=30)
    anomalias = database.listar_anomalias(limit=25)
    return templates.TemplateResponse(
        request,
        "inteligencia.html",
        {
            "desde": desde.strip(),
            "ate": ate.strip(),
            "ranking": ranking,
            "temas": temas,
            "lrf": lrf,
            "anomalias": anomalias,
        },
    )


@app.post("/revisao/so-mencao/{edicao_id}/triagem")
def triagem_lote_edicao(edicao_id: int) -> RedirectResponse:
    """#5 — triagem IA em lote das menções da edição."""
    from ai_processor import triar_ruidos_lote

    database.init_db()
    ed = None
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, titulo FROM edicoes WHERE id = ?", (edicao_id,)
        ).fetchone()
        if row:
            ed = dict(row)
    if not ed:
        return RedirectResponse(url="/revisao/so-mencao", status_code=303)
    mencoes = database.get_mencoes_edicao(edicao_id, limit=15)
    result = triar_ruidos_lote(mencoes, titulo=ed.get("titulo") or "")
    if result is not None:
        import json as _json

        database.set_setting(
            f"triagem_lote_{edicao_id}",
            _json.dumps(result, ensure_ascii=False),
        )
    return RedirectResponse(url=f"/revisao/so-mencao?edicao={edicao_id}", status_code=303)


@app.get("/perguntar", response_class=HTMLResponse)
def perguntar_atos(
    request: Request,
    q: str = Query("", description="Pergunta em linguagem natural"),
) -> HTMLResponse:
    """Chat simples: busca smart + IA com citações."""
    database.init_db()
    pergunta = q.strip()
    resposta = None
    citacoes: list = []
    erro = None
    if pergunta:
        if not getattr(SETTINGS, "ai_chat", True):
            erro = "Chat de IA desligado (AI_CHAT=false)."
        else:
            from ai_processor import ia_disponivel, responder_pergunta_atos
            from inteligencia import rankear_publicacoes

            if not ia_disponivel():
                erro = "IA indisponível (chave, refine ou circuit breaker)."
            else:
                base = database.buscar_publicacoes_texto(limit=400)
                contextos = rankear_publicacoes(pergunta, base, limit=8)
                out = responder_pergunta_atos(pergunta, contextos)
                if out:
                    resposta = out.get("resposta")
                    citacoes = out.get("citacoes") or []
                else:
                    erro = "A IA não retornou resposta. Tente de novo."
    return templates.TemplateResponse(
        request,
        "perguntar.html",
        {
            "q": pergunta,
            "resposta": resposta,
            "citacoes": citacoes,
            "erro": erro,
        },
    )


@app.post("/operacao/quarentena/{edicao_id}/liberar")
def liberar_quarentena_edicao(edicao_id: int) -> RedirectResponse:
    """Zera falhas e devolve a edição à fila automática."""
    database.init_db()
    ok = database.liberar_quarentena(edicao_id)
    if ok:
        database.log_job(
            "quarentena",
            "concluido",
            edicao_id=edicao_id,
            mensagem="Edição liberada da quarentena — volta à fila",
        )
    return RedirectResponse(url="/operacao", status_code=303)


@app.get("/detecoes", response_class=RedirectResponse)
def detecoes_redirect() -> RedirectResponse:
    """Rota legada: lista de detecções virou a home de Atos."""
    return RedirectResponse(url="/", status_code=302)


@app.get("/revisao/so-mencao", response_class=HTMLResponse)
def revisao_so_mencao(
    request: Request,
    todas: str = Query("", description="1 = inclui revisadas/ignoradas"),
    edicao: int = Query(0, description="id para destacar triagem"),
) -> HTMLResponse:
    """Fila humana: tem_inaja sem publicações."""
    database.init_db()
    incluir = todas.strip() in {"1", "true", "sim", "yes"}
    raw = database.listar_edicoes_so_mencao(incluir_revisadas=incluir)
    # Enriquece com JSON da auditoria IA / FN / triagem
    edicoes = []
    for row in raw:
        item = dict(row)
        aud_raw = item.get("auditoria_so_mencao") or ""
        item["auditoria"] = _parse_json_field(aud_raw)
        fn_raw = item.get("fn_sugestao") or ""
        item["fn"] = _parse_json_field(fn_raw) or item["auditoria"]
        tri_raw = database.get_setting(f"triagem_lote_{item['id']}", "")
        item["triagem"] = _parse_json_field(tri_raw)
        edicoes.append(item)
    # Contagens globais
    with _conn() as conn:
        base = """
            SELECT COUNT(*) FROM edicoes e
            WHERE e.tem_inaja = 1
              AND NOT EXISTS (SELECT 1 FROM publicacoes p WHERE p.edicao_id = e.id)
        """
        total_all = conn.execute(base).fetchone()[0]
        pendentes = conn.execute(
            base
            + " AND (e.revisao_so_mencao IS NULL OR e.revisao_so_mencao = '' "
            "OR e.revisao_so_mencao = 'pendente')"
        ).fetchone()[0]
        revisadas = conn.execute(
            base + " AND e.revisao_so_mencao = 'revisada'"
        ).fetchone()[0]
        ignoradas = conn.execute(
            base + " AND e.revisao_so_mencao = 'ignorada'"
        ).fetchone()[0]
    return templates.TemplateResponse(
        request,
        "so_mencao.html",
        {
            "edicoes": edicoes,
            "incluir_revisadas": incluir,
            "destaque_edicao": int(edicao or 0),
            "stats": {
                "total": len(edicoes),
                "pendentes": pendentes,
                "revisadas": revisadas,
                "ignoradas": ignoradas,
                "total_all": total_all,
            },
        },
    )


@app.post("/revisao/so-mencao/{edicao_id}")
async def revisao_so_mencao_marcar(
    edicao_id: int,
    request: Request,
) -> RedirectResponse:
    form = await request.form()
    status = str(form.get("status", "revisada"))
    nxt = str(form.get("next", "/revisao/so-mencao"))
    try:
        database.marcar_revisao_so_mencao(edicao_id, status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Status inválido")
    if not nxt.startswith("/"):
        nxt = "/revisao/so-mencao"
    return RedirectResponse(nxt, status_code=303)


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

def _parse_json_field(raw) -> dict | list | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        import json as _json

        return _json.loads(raw)
    except Exception:
        return None


def _enrich_publicacao_row(row) -> dict:
    item = dict(row)
    item["partes"] = _parse_json_field(item.get("partes_ia"))
    item["checklist"] = _parse_json_field(item.get("checklist_ia"))
    item["validacao"] = _parse_json_field(item.get("validacao_ia"))
    return item


@app.get("/edicoes/{edicao_id}", response_class=HTMLResponse)
def edicao_detail(
    request: Request,
    edicao_id: int,
    painel: str = Query("", description="similares|timeline"),
    pub: int = Query(0, description="id da publicação do painel"),
) -> HTMLResponse:
    with _conn() as conn:
        edicao = _row_or_404(conn, "SELECT * FROM edicoes WHERE id = ?", (edicao_id,))
        publicacoes_raw = conn.execute(
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

    publicacoes = [_enrich_publicacao_row(r) for r in publicacoes_raw]
    painel_data = None
    painel_tipo = (painel or "").strip().casefold()
    painel_pub_id = int(pub or 0)
    if painel_tipo in {"similares", "timeline"} and painel_pub_id:
        painel_data = _build_painel_pub(painel_tipo, painel_pub_id)

    return templates.TemplateResponse(
        request,
        "edicao.html",
        {
            "edicao": edicao,
            "publicacoes": publicacoes,
            "mencoes": mencoes,
            "num_paginas": num_paginas,
            "painel_tipo": painel_tipo,
            "painel_pub_id": painel_pub_id,
            "painel_data": painel_data,
        },
    )


def _build_painel_pub(tipo: str, pub_id: int) -> dict | None:
    """Monta painel de similares (#8) ou timeline (#7)."""
    pub = database.get_publicacao_by_id(pub_id)
    if not pub:
        return None
    if tipo == "timeline":
        from ai_processor import narrar_linha_tempo

        rel = database.buscar_pubs_relacionadas(
            numero=pub.get("numero"),
            tipo=pub.get("tipo"),
            orgao=pub.get("orgao"),
            excluir_id=pub_id,
            limit=12,
        )
        cadeia = [pub] + rel
        # se só o próprio e sem número, tenta só por tipo contratual
        if len(cadeia) <= 1 and not (pub.get("numero") or "").strip():
            rel = database.buscar_pubs_relacionadas(
                tipo=pub.get("tipo") or "contrato",
                orgao=pub.get("orgao"),
                excluir_id=pub_id,
                limit=8,
            )
            cadeia = [pub] + rel
        narrativa = None
        if getattr(SETTINGS, "ai_timeline", True) and len(cadeia) >= 1:
            narrativa = narrar_linha_tempo(cadeia)
        return {
            "tipo": "timeline",
            "itens": cadeia,
            "narrativa": narrativa
            or (
                "Cadeia local (sem narrativa IA)."
                if len(cadeia) > 1
                else "Nenhum ato relacionado encontrado no acervo."
            ),
        }
    if tipo == "similares":
        from ai_processor import comparar_com_similares
        from inteligencia import query_similares_de_pub, rankear_publicacoes

        base = database.buscar_publicacoes_texto(limit=400)
        base = [b for b in base if b.get("id") != pub_id]
        q = query_similares_de_pub(pub)
        cands = rankear_publicacoes(q, base, limit=6)
        ia_out = None
        if getattr(SETTINGS, "ai_similares", True) and cands:
            ia_out = comparar_com_similares(pub, cands)
        return {
            "tipo": "similares",
            "candidatos": cands,
            "ia": ia_out,
        }
    return None


@app.head("/edicoes/{edicao_id}")
def edicao_detail_head(edicao_id: int) -> Response:
    with _conn() as conn:
        _row_or_404(conn, "SELECT id FROM edicoes WHERE id = ?", (edicao_id,))
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Status / Fila (unificado no Painel)
# ---------------------------------------------------------------------------

def _dados_fila_jobs() -> dict:
    """Resumo + grupos de jobs para o cockpit /status e /operacao?tab=fila."""
    with _conn() as conn:
        row = conn.execute(
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
        resumo = {
            "rodando": int(row["rodando"] or 0) if row else 0,
            "avisos": int(row["avisos"] or 0) if row else 0,
            "erros": int(row["erros"] or 0) if row else 0,
            "concluidos": int(row["concluidos"] or 0) if row else 0,
            "total": int(row["total"] or 0) if row else 0,
        }
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
        if job["atualizado_em"] and (
            not grupo["atualizado_em"] or job["atualizado_em"] > grupo["atualizado_em"]
        ):
            grupo["atualizado_em"] = job["atualizado_em"]

    grupos = sorted(
        grupos, key=lambda g: g["atualizado_em"] or "", reverse=True
    )
    return {"resumo": resumo, "jobs": jobs, "grupos": grupos}


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request) -> RedirectResponse:
    """Compat: Fila passa a viver no Painel (aba Fila)."""
    return RedirectResponse("/operacao?tab=fila", status_code=302)


@app.post("/status/limpar")
def limpar_status_jobs() -> RedirectResponse:
    database.clear_jobs_history()
    return RedirectResponse("/operacao?tab=fila", status_code=303)



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
# Admin (senha de porta — cookie de sessão da aba Admin)
# ---------------------------------------------------------------------------

import hashlib
import os as _os
import secrets as _secrets
from urllib.parse import quote as _url_quote

# Senha da porta Admin (sobrescrevível por ADMIN_GATE_PASSWORD no .env)
ADMIN_GATE_PASSWORD = (_os.getenv("ADMIN_GATE_PASSWORD", "1999") or "1999").strip()
ADMIN_GATE_COOKIE = "monitor_admin_gate"
_ADMIN_LOCKED_DETAIL = "Admin bloqueado. Faça login na área Admin."
_ADMIN_SESSION_HOURS = 12
# Rate limit login: N tentativas por IP em janela de minutos
_ADMIN_LOGIN_MAX = 8
_ADMIN_LOGIN_WINDOW_S = 15 * 60
_admin_login_hits: dict[str, list[float]] = {}


class AdminGateError(Exception):
    """Sessão admin ausente/expirada — API → 401 JSON; forms → redirect /admin."""


@app.exception_handler(AdminGateError)
async def _admin_gate_error_handler(request: Request, exc: AdminGateError) -> Response:
    if request.url.path.startswith("/admin/api/"):
        return JSONResponse(status_code=401, content={"detail": _ADMIN_LOCKED_DETAIL})
    # Form POST/navegação: volta à tela de senha (evita página JSON crua).
    return RedirectResponse(
        "/admin?msg=" + _url_quote("Sessão admin expirada. Entre novamente."),
        status_code=303,
    )


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def _admin_login_allowed(ip: str) -> bool:
    agora = time.time()
    hits = [t for t in _admin_login_hits.get(ip, []) if agora - t < _ADMIN_LOGIN_WINDOW_S]
    _admin_login_hits[ip] = hits
    return len(hits) < _ADMIN_LOGIN_MAX


def _admin_login_register(ip: str) -> None:
    _admin_login_hits.setdefault(ip, []).append(time.time())


def _admin_create_session() -> str:
    """Gera token aleatório de sessão (não deriva da senha)."""
    from datetime import datetime, timedelta

    token = _secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(hours=_ADMIN_SESSION_HOURS)).isoformat(
        timespec="seconds"
    )
    database.set_setting("admin_session_token", token)
    database.set_setting("admin_session_exp", exp)
    return token


def _admin_clear_session() -> None:
    database.set_setting("admin_session_token", "")
    database.set_setting("admin_session_exp", "")


def _admin_unlocked(request: Request) -> bool:
    from datetime import datetime

    cookie = request.cookies.get(ADMIN_GATE_COOKIE) or ""
    token = database.get_setting("admin_session_token", "") or ""
    exp_s = database.get_setting("admin_session_exp", "") or ""
    if not cookie or not token or not exp_s:
        return False
    try:
        exp = datetime.fromisoformat(exp_s)
    except ValueError:
        return False
    if exp < datetime.now():
        return False
    if len(cookie) != len(token):
        return False
    return _secrets.compare_digest(cookie, token)


def _admin_require(request: Request) -> None:
    if not _admin_unlocked(request):
        raise AdminGateError()


def _admin_cookie_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"


def _admin_bool_setting(chave: str, fallback: bool) -> bool:
    raw = (database.get_setting(chave, "") or "").strip().lower()
    if not raw:
        return fallback
    return raw in {"1", "true", "yes", "on", "sim"}


def _admin_ctx(msg: str = "", teste_resultado: str | None = None) -> dict:
    api_key = database.get_setting("opencode_api_key", "") or SETTINGS.opencode_api_key
    model = database.get_setting("opencode_model", "") or SETTINGS.opencode_model
    api_key_masked = (
        f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else ("***" if api_key else "")
    )
    ai_refine = _admin_bool_setting("ai_refine_publications", SETTINGS.ai_refine_publications)
    absence_raw = database.get_setting("absence_alert_days", "")
    try:
        absence_days = int(absence_raw) if absence_raw.strip() else int(SETTINGS.absence_alert_days)
    except (TypeError, ValueError):
        absence_days = int(SETTINGS.absence_alert_days)
    try:
        from agente import status_agente

        agente_st = status_agente()
    except Exception:
        agente_st = {}
    return {
        "api_key_configured": bool(api_key),
        "api_key_masked": api_key_masked,
        "model": model or "deepseek-v4-flash",
        "ai_ativo": bool(api_key) and ai_refine,
        "ai_refine": ai_refine,
        "extra_terms": database.get_setting("extra_terms", "")
        or ",".join(SETTINGS.extra_terms),
        "ignore_terms": database.get_setting("ignore_terms", "")
        or ",".join(SETTINGS.ignore_context_terms),
        "absence_alert_days": absence_days,
        "webhook_url": database.get_setting("webhook_url", "") or SETTINGS.webhook_url,
        "webhooks": database.get_webhooks(),
        "msg": msg,
        "teste_resultado": teste_resultado,
        "agente": agente_st,
        "agente_env": {
            "pulse_s": SETTINGS.agente_pulse_segundos,
            "cerebro_min": SETTINGS.agente_cerebro_minutos,
            "max_ocr": SETTINGS.agente_max_ocr_por_ciclo,
            "max_ia_hora": SETTINGS.agente_max_ia_por_hora,
            "auto_lock": SETTINGS.agente_auto_limpar_lock,
            "auto_jobs": SETTINGS.agente_auto_limpar_jobs,
            "no_bot": SETTINGS.agente_no_bot,
            "notificar": SETTINGS.agente_notificar,
            "lock_max_min": SETTINGS.agente_lock_max_minutos,
            "job_max_min": SETTINGS.agente_job_max_minutos,
        },
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, msg: str = "") -> HTMLResponse:
    if not _admin_unlocked(request):
        return templates.TemplateResponse(
            request,
            "admin.html",
            {"admin_locked": True, "msg": msg, "login_erro": ""},
        )
    ctx = _admin_ctx(msg=msg)
    ctx["admin_locked"] = False
    return templates.TemplateResponse(request, "admin.html", ctx)


@app.post("/admin/login")
async def admin_login(request: Request) -> Response:
    ip = _client_ip(request)
    if not _admin_login_allowed(ip):
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "admin_locked": True,
                "msg": "",
                "login_erro": "Muitas tentativas. Aguarde 15 minutos.",
            },
            status_code=429,
        )
    form = await request.form()
    senha = str(form.get("senha", "")).strip()
    # compare_digest exige mesmo comprimento; fallback evita exceção em senha vazia
    expected = ADMIN_GATE_PASSWORD
    ok = bool(senha) and len(senha) == len(expected) and _secrets.compare_digest(
        senha, expected
    )
    if not ok:
        _admin_login_register(ip)
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "admin_locked": True,
                "msg": "",
                "login_erro": "Senha incorreta.",
            },
            status_code=401,
        )
    # sucesso: limpa tentativas deste IP
    _admin_login_hits.pop(ip, None)
    token = _admin_create_session()
    resp = RedirectResponse(
        "/admin?msg=" + _url_quote("Admin desbloqueado"),
        status_code=303,
    )
    resp.set_cookie(
        ADMIN_GATE_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=_admin_cookie_secure(request),
        max_age=60 * 60 * _ADMIN_SESSION_HOURS,
        path="/",
    )
    return resp


@app.post("/admin/logout")
def admin_logout() -> Response:
    _admin_clear_session()
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(ADMIN_GATE_COOKIE, path="/")
    return resp


@app.post("/admin/backup")
def admin_backup(request: Request) -> RedirectResponse:
    _admin_require(request)
    try:
        path = database.backup_database()
        msg = f"Backup criado: {path.name}"
    except Exception as exc:
        msg = f"Falha no backup: {exc}"
    return RedirectResponse(f"/admin?msg={_url_quote(msg)}", status_code=303)


@app.post("/admin/limpar-edicoes-antigas")
def limpar_edicoes_antigas(request: Request) -> RedirectResponse:
    _admin_require(request)
    database.delete_hnetsistemas_edicoes()
    return RedirectResponse(
        "/admin?msg=" + _url_quote("Edições de sistema antigo removidas com sucesso!"),
        status_code=303,
    )


@app.post("/admin/salvar")
async def admin_salvar(request: Request) -> RedirectResponse:
    _admin_require(request)
    form = await request.form()
    fields = {
        "opencode_api_key": str(form.get("opencode_api_key", "")),
        "opencode_model": str(form.get("opencode_model", "")),
        "extra_terms": str(form.get("extra_terms", "")),
        "ignore_terms": str(form.get("ignore_terms", "")),
        "webhook_url": str(form.get("webhook_url", "")),
        "absence_alert_days": str(form.get("absence_alert_days", "30")),
    }
    for chave, valor in fields.items():
        if valor.strip():
            database.set_setting(chave, valor.strip())
    # Toggle AI refine (hidden false + checkbox true)
    try:
        vals = [str(v).strip().lower() for v in form.getlist("ai_refine_publications")]
    except Exception:
        vals = [str(form.get("ai_refine_publications", "")).strip().lower()]
    if vals:
        ai_on = "true" in vals or "on" in vals or "1" in vals
        database.set_setting("ai_refine_publications", "true" if ai_on else "false")
        try:
            object.__setattr__(SETTINGS, "ai_refine_publications", ai_on)
        except Exception:
            pass
    absence_raw = fields.get("absence_alert_days", "").strip()
    if absence_raw:
        try:
            object.__setattr__(SETTINGS, "absence_alert_days", max(1, min(365, int(absence_raw))))
        except (TypeError, ValueError, Exception):
            pass
    if fields.get("opencode_api_key", "").strip():
        from ai_processor import reset_auth_circuit

        reset_auth_circuit()
    return RedirectResponse(
        "/admin?msg=" + _url_quote("Configurações salvas com sucesso!"),
        status_code=303,
    )


@app.post("/admin/testar")
def admin_testar(request: Request) -> HTMLResponse:
    _admin_require(request)
    from ai_processor import _api_key, _auth_bloqueada, _extrair_publicacao, reset_auth_circuit

    reset_auth_circuit()
    key = _api_key()
    if not key:
        resultado = "Nenhuma API Key configurada (.env OPENCODE_API_KEY ou Admin)."
    else:
        try:
            r = _extrair_publicacao(
                "DECRETO Nº 001/2026 - Prefeitura Municipal de Inajá - PR.",
                timeout=15,
            )
            if _auth_bloqueada:
                resultado = (
                    "Falha de autenticação (401 Invalid API key). "
                    "Renove a chave em opencode.ai e atualize OPENCODE_API_KEY."
                )
            elif r:
                resultado = f"OK — Resposta recebida: {str(r)[:200]}"
            else:
                resultado = "Sem resposta útil da IA (timeout/JSON). Veja o log do servidor."
        except Exception as exc:
            resultado = f"Erro: {exc}"

    ctx = _admin_ctx(teste_resultado=resultado)
    ctx["admin_locked"] = False
    return templates.TemplateResponse(request, "admin.html", ctx)


@app.post("/admin/webhook/adicionar")
async def admin_webhook_adicionar(request: Request) -> RedirectResponse:
    _admin_require(request)
    form = await request.form()
    url = str(form.get("webhook_url", "")).strip()
    descricao = str(form.get("webhook_descricao", "")).strip()
    if url:
        database.upsert_webhook(url, descricao)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/webhook/{webhook_id}/remover")
def admin_webhook_remover(request: Request, webhook_id: int) -> RedirectResponse:
    _admin_require(request)
    database.delete_webhook(webhook_id)
    return RedirectResponse("/admin", status_code=303)


# ---- Admin JSON API (Agente + ferramentas) ----

@app.get("/admin/api/agente/status")
def admin_api_agente_status(request: Request) -> JSONResponse:
    _admin_require(request)
    from agente import status_agente, listar_log

    st = status_agente()
    st["log"] = listar_log(40)
    return JSONResponse(st)


@app.post("/admin/api/agente/on")
def admin_api_agente_on(request: Request) -> JSONResponse:
    _admin_require(request)
    from agente import set_agente_ativo, status_agente

    set_agente_ativo(True)
    return JSONResponse({"ok": True, "status": status_agente()})


@app.post("/admin/api/agente/off")
def admin_api_agente_off(request: Request) -> JSONResponse:
    _admin_require(request)
    from agente import set_agente_ativo, status_agente

    set_agente_ativo(False)
    return JSONResponse({"ok": True, "status": status_agente()})


@app.post("/admin/api/agente/modo")
async def admin_api_agente_modo(request: Request) -> JSONResponse:
    _admin_require(request)
    from agente import MODOS, set_agente_modo, status_agente

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido. Envie {\"modo\": \"...\"}.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON inválido. Envie {\"modo\": \"...\"}.")
    modo = str(body.get("modo", "auto")).strip().casefold()
    if modo not in MODOS:
        raise HTTPException(status_code=400, detail=f"Modo inválido. Use: {', '.join(MODOS)}")
    set_agente_modo(modo)
    return JSONResponse({"ok": True, "status": status_agente()})


@app.post("/admin/api/agente/pulse")
def admin_api_agente_pulse(request: Request) -> JSONResponse:
    _admin_require(request)
    from agente import format_ciclo, run_pulse, status_agente

    res = run_pulse(force=True)
    return JSONResponse(
        {
            "ok": True,
            "texto": format_ciclo(res),
            "acoes": [
                {"acao": a.acao, "ok": a.ok, "detalhe": a.detalhe, "nivel": a.nivel}
                for a in res.acoes
            ],
            "status": status_agente(),
        }
    )


@app.post("/admin/api/agente/cerebro")
def admin_api_agente_cerebro(request: Request) -> JSONResponse:
    _admin_require(request)
    from agente import format_ciclo, run_cerebro, status_agente

    res = run_cerebro(force=True)
    return JSONResponse(
        {
            "ok": True,
            "texto": format_ciclo(res),
            "acoes": [
                {"acao": a.acao, "ok": a.ok, "detalhe": a.detalhe, "nivel": a.nivel}
                for a in res.acoes
            ],
            "status": status_agente(),
        }
    )


@app.post("/admin/api/agente/once")
def admin_api_agente_once(request: Request) -> JSONResponse:
    _admin_require(request)
    from agente import format_ciclo, run_cerebro, run_pulse, status_agente

    p = run_pulse(force=True)
    c = run_cerebro(force=True)
    return JSONResponse(
        {
            "ok": True,
            "pulse": format_ciclo(p),
            "cerebro": format_ciclo(c),
            "status": status_agente(),
        }
    )


@app.post("/admin/api/fila/processar")
async def admin_api_fila_processar(request: Request) -> JSONResponse:
    """Processa N pendentes prioritárias (OCR real)."""
    _admin_require(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    limite = int((body or {}).get("limite") or 5)
    limite = max(1, min(20, limite))
    from agente import _pick_pendentes, log_acao, modo_efetivo
    from pipeline import processar_edicao_por_id

    picks = _pick_pendentes(limite)
    resultados = []
    for row in picks:
        eid = int(row["id"])
        try:
            r = processar_edicao_por_id(
                eid, force_ocr=True, fast_ocr=True, notificar_se_encontrado=False
            )
            det = {
                "id": eid,
                "data": row.get("data_publicacao"),
                "ok": r is not None,
                "inaja": bool(r.encontrado) if r else False,
                "pubs": len(r.publicacoes) if r else 0,
            }
        except Exception as exc:
            det = {"id": eid, "ok": False, "erro": str(exc)[:120]}
        resultados.append(det)
        log_acao(
            ciclo="admin",
            modo=modo_efetivo(),
            acao="processar_pendente",
            detalhe=str(det),
            ok=bool(det.get("ok")),
        )
    return JSONResponse({"ok": True, "processadas": len(resultados), "itens": resultados})


@app.post("/admin/api/settings/desde")
async def admin_api_settings_desde(request: Request) -> JSONResponse:
    """Define AUTO_PROCESS_DESDE em settings + SETTINGS em memória."""
    _admin_require(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")
    desde = str((body or {}).get("desde") or "").strip()
    if desde and (len(desde) != 10 or desde[4] != "-" or desde[7] != "-"):
        raise HTTPException(400, "Use YYYY-MM-DD ou string vazia")
    database.set_setting("auto_process_desde", desde)
    try:
        object.__setattr__(SETTINGS, "auto_process_desde", desde)
    except Exception:
        pass
    return JSONResponse({"ok": True, "desde": desde})


@app.post("/admin/api/edicao/{edicao_id}/reprocessar-cache")
def admin_api_reprocessar_cache(request: Request, edicao_id: int) -> JSONResponse:
    """Reprocessa detecção a partir do .ocr.json e devolve diff de pubs."""
    _admin_require(request)
    database.init_db()
    with database.connect() as c:
        antes = [
            dict(r)
            for r in c.execute(
                "SELECT id, tipo, numero, orgao, valor FROM publicacoes WHERE edicao_id=?",
                (edicao_id,),
            ).fetchall()
        ]
    from pipeline import reprocessar_deteccao_de_cache

    try:
        r = reprocessar_deteccao_de_cache(edicao_id, notificar_se_encontrado=False)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    with database.connect() as c:
        depois = [
            dict(r)
            for r in c.execute(
                "SELECT id, tipo, numero, orgao, valor FROM publicacoes WHERE edicao_id=?",
                (edicao_id,),
            ).fetchall()
        ]
    chave = lambda p: f"{p.get('tipo') or ''}|{p.get('numero') or ''}|{p.get('orgao') or ''}"
    set_a = {chave(p) for p in antes}
    set_d = {chave(p) for p in depois}
    return JSONResponse(
        {
            "ok": True,
            "edicao_id": edicao_id,
            "inaja": bool(r.encontrado) if r else False,
            "pubs_antes": len(antes),
            "pubs_depois": len(depois),
            "entraram": [p for p in depois if chave(p) not in set_a],
            "sairam": [p for p in antes if chave(p) not in set_d],
        }
    )


@app.get("/admin/api/fn")
def admin_api_fn(request: Request, limit: int = 20) -> JSONResponse:
    """Lista só-menção / FN para revisão no Admin."""
    _admin_require(request)
    rows = [dict(r) for r in database.listar_edicoes_so_mencao(limit=limit)]
    return JSONResponse({"ok": True, "itens": rows})


@app.get("/admin/api/diagnostico")
def admin_api_diagnostico(request: Request) -> JSONResponse:
    _admin_require(request)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    try:
        from scripts import _diagnostico as diag  # type: ignore
    except Exception:
        # scripts com underscore: import via runpy path
        import importlib.util

        path = BASE_DIR / "scripts" / "_diagnostico.py"
        spec = importlib.util.spec_from_file_location("diag", path)
        diag = importlib.util.module_from_spec(spec)  # type: ignore
        assert spec and spec.loader
        spec.loader.exec_module(diag)  # type: ignore
    with redirect_stdout(buf):
        diag.main()
    return JSONResponse({"ok": True, "texto": buf.getvalue()})


@app.get("/admin/api/qualidade")
def admin_api_qualidade(request: Request, modo: str = "tudo") -> JSONResponse:
    _admin_require(request)
    import io
    from contextlib import redirect_stdout
    import importlib.util

    path = BASE_DIR / "scripts" / "_qualidade.py"
    spec = importlib.util.spec_from_file_location("qualidade", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore
    buf = io.StringIO()
    # monkeypatch argv
    import sys

    old = sys.argv
    sys.argv = ["_qualidade.py", "--modo", modo, "--limite", "25"]
    try:
        with redirect_stdout(buf):
            mod.main()
    finally:
        sys.argv = old
    return JSONResponse({"ok": True, "texto": buf.getvalue()})


@app.post("/admin/api/notificar/testar")
def admin_api_notificar_testar(request: Request) -> JSONResponse:
    """Grava notificação de teste em arquivo e devolve canal usado."""
    _admin_require(request)
    from notifier import enviar_teste

    try:
        info = enviar_teste()
    except Exception as exp:
        return JSONResponse(
            {"ok": False, "erro": str(exp)[:200]},
            status_code=500,
        )
    return JSONResponse(info)


@app.post("/admin/api/ferramenta/{nome}")
def admin_api_ferramenta(request: Request, nome: str) -> JSONResponse:
    """Ferramentas: lock, jobs, backup, limpar-dry, disco-dry, disco-aplicar."""
    _admin_require(request)
    nome = nome.strip().lower()
    if nome == "lock":
        from process_lock import DEFAULT_LOCK

        if DEFAULT_LOCK.exists():
            DEFAULT_LOCK.unlink()
            return JSONResponse({"ok": True, "msg": f"Lock removido: {DEFAULT_LOCK}"})
        return JSONResponse({"ok": True, "msg": "Nenhum lock encontrado."})
    if nome == "jobs":
        n = database.cleanup_stuck_jobs(max_hours=0)
        return JSONResponse({"ok": True, "msg": f"{n} job(s) limpo(s)."})
    if nome == "backup":
        path = database.backup_database()
        return JSONResponse({"ok": True, "msg": f"Backup: {path}"})
    if nome == "limpar-dry":
        import io
        from contextlib import redirect_stdout
        import importlib.util
        import sys

        path = BASE_DIR / "scripts" / "_limpar_processados.py"
        spec = importlib.util.spec_from_file_location("limpar", path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore
        assert spec and spec.loader
        spec.loader.exec_module(mod)  # type: ignore
        old = sys.argv
        sys.argv = ["_limpar_processados.py", "--dry-run"]
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                mod.main()
        finally:
            sys.argv = old
        return JSONResponse({"ok": True, "msg": buf.getvalue()})
    if nome in {"disco-dry", "disco-aplicar"}:
        import importlib.util

        path = BASE_DIR / "scripts" / "_limpeza_disco.py"
        spec = importlib.util.spec_from_file_location("disco", path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore
        assert spec and spec.loader
        spec.loader.exec_module(mod)  # type: ignore
        dry = nome == "disco-dry"
        r1 = mod.limpar_pdfs_sem_inaja(meses=18, dry_run=dry)
        r2 = mod.reter_backups(manter=10, dry_run=dry)
        return JSONResponse({"ok": True, "pdfs": r1, "backups": r2})
    raise HTTPException(status_code=400, detail=f"Ferramenta desconhecida: {nome}")


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


@app.get("/api/automacao")
def api_automacao() -> dict:
    """Última/próxima varredura WEB, ciclo BOT e fila de OCR."""
    return database.get_status_automacao()


@app.get("/api/agente/resumo")
def api_agente_resumo() -> dict:
    """Resumo público do agente (sem segredos) — chips do menu superior."""
    try:
        from agente import agente_esta_ativo, modo_efetivo, resolver_modo_auto

        database.init_db()
        modo = modo_efetivo()
        efetivo = resolver_modo_auto() if modo == "auto" else modo
        return {
            "ativo": agente_esta_ativo(),
            "modo": modo,
            "modo_efetivo": efetivo,
            "ultimo_pulse": database.get_setting("agente_ultimo_pulse", ""),
            "ultimo_cerebro": database.get_setting("agente_ultimo_cerebro", ""),
        }
    except Exception as exc:
        return {"ativo": False, "erro": str(exc)[:120]}


@app.get("/api/buscar")
def api_buscar(
    q: str = Query("", description="Consulta"),
    limit: int = Query(30, ge=1, le=100),
) -> list[dict]:
    """Busca inteligente (ranking por termos) nas publicações."""
    from inteligencia import rankear_publicacoes

    database.init_db()
    base = database.buscar_publicacoes_texto(limit=500)
    if not q.strip():
        return base[:limit]
    return rankear_publicacoes(q.strip(), base, limit=limit)


@app.get("/api/resumo-diario")
def api_resumo_diario() -> dict:
    return database.get_resumo_diario()


@app.get("/api/health")
def api_health() -> dict:
    """Health-check simples para monitoramento / Docker / uptime."""
    auto = database.get_status_automacao()
    ok = True
    problemas: list[str] = []
    if not auto.get("bot_vivo"):
        problemas.append("bot_offline")
    if int(auto.get("jobs_rodando") or 0) > 10:
        problemas.append("muitos_jobs_rodando")
    # Interface sozinha está ok; bot offline vira degraded, não fatal
    status = "ok" if auto.get("bot_vivo") else "degraded"
    return {
        "status": status,
        "ok": ok,
        "bot_vivo": bool(auto.get("bot_vivo")),
        "pendentes_ocr": int(auto.get("pendentes_ocr") or 0),
        "fila_proximo_ciclo": int(auto.get("fila_proximo_ciclo") or 0),
        "jobs_rodando": int(auto.get("jobs_rodando") or 0),
        "web_ultimo": auto.get("web_ultimo") or None,
        "bot_ultimo": auto.get("bot_ultimo") or None,
        "bot_heartbeat": auto.get("bot_heartbeat") or None,
        "problemas": problemas,
        "auto_process": bool(auto.get("auto_process")),
    }


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
        database.update_job(
            ia_job,
            "rodando",
            mensagem="Refinando publicações com IA...",
            progress_current=10,
            progress_total=100,
            progress_step="ia",
        )
        refinadas, _stats = refinar_publicacoes(pubs)
        database.insert_publicacoes(edicao_id, refinadas)
        if texto_path:
            database.salvar_arquivos_atos_locais(texto_path, refinadas)
        database.update_job(ia_job, "concluido", mensagem="Refinamento IA concluído", progress_current=100, progress_total=100, progress_step="ia")

    _task_executor.submit(_refinar)
    if format == "json" or (request and request.headers.get("accept", "").startswith("application/json")):
        return {"status": "started", "edicao_id": edicao_id, "job_etapa": "refinando com IA"}
    return RedirectResponse(f"/edicoes/{edicao_id}", status_code=303)

