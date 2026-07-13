# -*- coding: utf-8 -*-
"""Agente de vigilância do Monitor de Atos.

Dois relógios:
  - Pulse (barato): saúde, lock, jobs, alertas
  - Cérebro (caro): OCR seletivo, re-IA, qualidade

Modos: escudo | formiga | cirurgiao | sentinela | auto
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from config import SETTINGS
import database
from process_lock import (
    DEFAULT_LOCK,
    is_lock_held,
    lock_age_minutes,
    lock_holder_text,
    lock_status,
)

logger = logging.getLogger(__name__)

MODOS = ("escudo", "formiga", "cirurgiao", "sentinela", "auto")

# settings keys
_K_MODO = "agente_modo"
_K_ATIVO = "agente_ativo"
_K_PULSE = "agente_ultimo_pulse"
_K_CEREBRO = "agente_ultimo_cerebro"
_K_IA_HORA = "agente_ia_hora"  # "YYYYMMDDHH:count"
_K_OCR_DIA = "agente_ocr_dia"  # "YYYYMMDD:count"
_K_RE_IA_DIA = "agente_re_ia_dia"  # "YYYYMMDD:count"
_K_DIGEST_DIA = "agente_ultimo_digest_dia"  # YYYYMMDD
_K_ALERTA = "agente_alerta_"  # prefix + key → iso cooldown


@dataclass
class AcaoResultado:
    acao: str
    ok: bool = True
    detalhe: str = ""
    nivel: str = "info"  # info | warn | erro | acao


@dataclass
class CicloResultado:
    ciclo: str
    modo: str
    acoes: list[AcaoResultado] = field(default_factory=list)
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def add(self, acao: str, ok: bool = True, detalhe: str = "", nivel: str = "info") -> None:
        self.acoes.append(AcaoResultado(acao=acao, ok=ok, detalhe=detalhe, nivel=nivel))


def _now() -> datetime:
    return datetime.now()


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except Exception:
        return None


def modo_efetivo() -> str:
    """Modo em settings DB (override) ou .env."""
    db_modo = (database.get_setting(_K_MODO, "") or "").strip().casefold()
    if db_modo in MODOS:
        return db_modo
    m = (SETTINGS.agente_modo or "auto").strip().casefold()
    return m if m in MODOS else "auto"


def agente_esta_ativo() -> bool:
    v = database.get_setting(_K_ATIVO, "")
    if v != "":
        return v.strip().casefold() in {"1", "true", "sim", "yes", "on"}
    return bool(SETTINGS.agente_ativo)


def set_agente_ativo(ativo: bool) -> None:
    database.set_setting(_K_ATIVO, "true" if ativo else "false")


def set_agente_modo(modo: str) -> None:
    m = (modo or "auto").strip().casefold()
    if m not in MODOS:
        raise ValueError(f"Modo inválido: {modo}. Use: {', '.join(MODOS)}")
    database.set_setting(_K_MODO, m)


def resolver_modo_auto() -> str:
    """Heurística: manhã de jornal → formiga; senão cirurgião se há fracos; senão escudo."""
    with database.connect() as c:
        pend = c.execute(
            "SELECT COUNT(*) FROM edicoes WHERE ocr_processado=0"
        ).fetchone()[0]
        fracos = c.execute(
            """
            SELECT COUNT(*) FROM publicacoes
            WHERE resumo_ia IS NULL OR trim(resumo_ia)=''
               OR valor IS NULL OR trim(valor)=''
            """
        ).fetchone()[0]
        hoje = date.today().isoformat()
        ed_hoje = c.execute(
            "SELECT COUNT(*) FROM edicoes WHERE data_publicacao=?", (hoje,)
        ).fetchone()[0]
    hora = _now().hour
    # 6h–14h e edição de hoje ou fila grande recente → formiga
    if 6 <= hora <= 14 and (ed_hoje > 0 or pend > 50):
        return "formiga"
    if fracos > 0 or pend > 0:
        return "cirurgiao" if fracos > pend // 10 else "formiga"
    return "escudo"


def log_acao(
    *,
    ciclo: str,
    modo: str,
    acao: str,
    detalhe: str = "",
    ok: bool = True,
    nivel: str = "info",
) -> None:
    try:
        with database.connect() as c:
            c.execute(
                """
                INSERT INTO agente_log (ciclo, modo, acao, nivel, detalhe, ok, criado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ciclo,
                    modo,
                    acao,
                    nivel,
                    (detalhe or "")[:2000],
                    1 if ok else 0,
                    _now().isoformat(timespec="seconds"),
                ),
            )
    except Exception:
        logger.debug("agente_log falhou", exc_info=True)
    lvl = logging.WARNING if nivel in {"warn", "erro"} else logging.INFO
    logger.log(lvl, "[agente/%s/%s] %s — %s", ciclo, modo, acao, detalhe)


def listar_log(limit: int = 30) -> list[dict]:
    database.init_db()
    with database.connect() as c:
        rows = c.execute(
            """
            SELECT id, criado_em, ciclo, modo, acao, nivel, detalhe, ok
            FROM agente_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(200, limit)),),
        ).fetchall()
        return [dict(r) for r in rows]


def status_agente() -> dict[str, Any]:
    database.init_db()
    modo = modo_efetivo()
    modo_run = resolver_modo_auto() if modo == "auto" else modo
    return {
        "ativo": agente_esta_ativo(),
        "modo_config": modo,
        "modo_efetivo": modo_run,
        "ultimo_pulse": database.get_setting(_K_PULSE, ""),
        "ultimo_cerebro": database.get_setting(_K_CEREBRO, ""),
        "ia_hora": database.get_setting(_K_IA_HORA, ""),
        "pulse_s": SETTINGS.agente_pulse_segundos,
        "cerebro_min": SETTINGS.agente_cerebro_minutos,
        "max_ocr": SETTINGS.agente_max_ocr_por_ciclo,
        "max_ocr_dia": SETTINGS.agente_max_ocr_por_dia,
        "ocr_hoje": _ocr_calls_hoje(),
        "noite": _hora_noturna(),
        "max_ia_hora": SETTINGS.agente_max_ia_por_hora,
        "no_bot": SETTINGS.agente_no_bot,
        "auto_process_desde": database.get_setting("auto_process_desde", "")
        or SETTINGS.auto_process_desde
        or "",
        "recentes": listar_log(8),
    }


def _alerta_permitido(chave: str) -> bool:
    raw = database.get_setting(_K_ALERTA + chave, "")
    dt = _parse_iso(raw)
    if not dt:
        return True
    return (_now() - dt).total_seconds() >= SETTINGS.agente_alerta_cooldown_min * 60


def _marcar_alerta(chave: str) -> None:
    database.set_setting(_K_ALERTA + chave, _now().isoformat(timespec="seconds"))


def _notificar(titulo: str, corpo: str) -> None:
    if not SETTINGS.agente_notificar:
        return
    try:
        texto = f"AGENTE: {titulo}\n{corpo}"
        alert_dir = SETTINGS.alert_dir
        alert_dir.mkdir(parents=True, exist_ok=True)
        path = alert_dir / f"{date.today().isoformat()}.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n{texto}\n")
    except Exception:
        logger.debug("notificar agente falhou", exc_info=True)


def _lock_age_minutes() -> float | None:
    """Compat: idade do arquivo de lock (não implica held)."""
    return lock_age_minutes()


def _lock_desc() -> str:
    """Texto curto para logs (held/holder/idade)."""
    st = lock_status()
    age = st.get("age_min")
    age_s = f"{age:.0f}min" if age is not None else "?"
    holder = st.get("holder") or "—"
    estado = "em uso" if st.get("held") else "livre"
    return f"{estado} holder={holder} idade≈{age_s}"


def _ia_calls_hora() -> int:
    raw = database.get_setting(_K_IA_HORA, "")
    if not raw or ":" not in raw:
        return 0
    bucket, n = raw.split(":", 1)
    agora = _now().strftime("%Y%m%d%H")
    if bucket != agora:
        return 0
    try:
        return int(n)
    except ValueError:
        return 0


def _inc_ia_calls(n: int = 1) -> None:
    agora = _now().strftime("%Y%m%d%H")
    cur = _ia_calls_hora()
    database.set_setting(_K_IA_HORA, f"{agora}:{cur + n}")


def _pode_ia(n: int = 1) -> bool:
    return _ia_calls_hora() + n <= SETTINGS.agente_max_ia_por_hora


def _re_ia_calls_hoje() -> int:
    raw = database.get_setting(_K_RE_IA_DIA, "")
    if not raw or ":" not in raw:
        return 0
    bucket, n = raw.split(":", 1)
    if bucket != _now().strftime("%Y%m%d"):
        return 0
    try:
        return int(n)
    except ValueError:
        return 0


def _inc_re_ia_dia(n: int = 1) -> None:
    hoje = _now().strftime("%Y%m%d")
    cur = _re_ia_calls_hoje()
    database.set_setting(_K_RE_IA_DIA, f"{hoje}:{cur + n}")


def _limite_re_ia_ciclo() -> int:
    """Orçamento multi-camada: min(ciclo, hora restante, dia restante, AI_MAX batch)."""
    if not bool(getattr(SETTINGS, "quality_re_ia_auto", True)):
        return 0
    max_ciclo = int(getattr(SETTINGS, "agente_max_re_ia_por_ciclo", 5) or 5)
    max_dia = int(getattr(SETTINGS, "agente_max_re_ia_por_dia", 40) or 40)
    remaining_hora = max(0, int(SETTINGS.agente_max_ia_por_hora) - _ia_calls_hora())
    remaining_dia = max(0, max_dia - _re_ia_calls_hoje())
    batch_cap = max(0, max_ciclo)
    ai_max = int(getattr(SETTINGS, "ai_max_calls_por_ciclo", 80) or 0)
    if ai_max > 0:
        batch_cap = min(batch_cap, ai_max)
    return min(batch_cap, remaining_hora, remaining_dia)


# ---------------------------------------------------------------------------
# PULSE
# ---------------------------------------------------------------------------

def run_pulse(*, force: bool = False) -> CicloResultado:
    database.init_db()
    modo_cfg = modo_efetivo()
    modo = resolver_modo_auto() if modo_cfg == "auto" else modo_cfg
    res = CicloResultado(ciclo="pulse", modo=modo)

    if not force and not agente_esta_ativo():
        res.add("skip", True, "agente desligado", "info")
        return res

    # sentinela: só observa
    only_observe = modo == "sentinela"

    # Jobs travados
    bot_vivo = False
    try:
        bot_vivo = bool(database.get_status_automacao().get("bot_vivo"))
    except Exception:
        pass

    if SETTINGS.agente_auto_limpar_jobs and not only_observe:
        try:
            # Se BOT morto, limpa qualquer job "rodando" (crash residual)
            if not bot_vivo:
                n = database.cleanup_stuck_jobs(max_hours=0)
            else:
                n = _cleanup_jobs_minutos(SETTINGS.agente_job_max_minutos)
            if n:
                res.add("limpar_jobs", True, f"{n} job(s) travado(s) → erro", "acao")
                log_acao(ciclo="pulse", modo=modo, acao="limpar_jobs", detalhe=f"n={n}")
        except Exception as exc:
            res.add("limpar_jobs", False, str(exc), "erro")

    # Lock: distingue arquivo residual vs OS lock realmente em uso
    age = _lock_age_minutes()
    held = False
    try:
        held = is_lock_held()
    except Exception:
        # fallback conservador: arquivo recente ≈ possivelmente em uso
        held = age is not None and age < 5

    if held:
        res.add(
            "lock_presente",
            True,
            f"em uso ({_lock_desc()})",
            "warn",
        )
        # Nunca apagar arquivo enquanto o OS lock estiver held (OCR longo legítimo)
        if age is not None and age >= SETTINGS.agente_lock_max_minutos:
            res.add(
                "lock_longo",
                True,
                f"lock ativo há ≈{age:.0f} min — não removido (processo vivo)",
                "warn",
            )
            log_acao(
                ciclo="pulse",
                modo=modo,
                acao="lock_longo",
                detalhe=_lock_desc(),
                nivel="warn",
            )
            if _alerta_permitido("lock_longo"):
                _notificar(
                    "Lock ativo há muito tempo",
                    f"processamento.lock em uso há ≈{age:.0f} min ({lock_holder_text() or 'sem label'}). "
                    "OCR pode estar lento; não foi removido automaticamente.",
                )
                _marcar_alerta("lock_longo")
    elif age is not None:
        # Arquivo residual (process_lock não apaga o arquivo ao liberar)
        res.add("lock_arquivo", True, f"residual livre idade≈{age:.0f} min", "info")
        if (
            SETTINGS.agente_auto_limpar_lock
            and not only_observe
            and age >= SETTINGS.agente_lock_max_minutos
        ):
            try:
                DEFAULT_LOCK.unlink(missing_ok=True)  # type: ignore[call-arg]
            except TypeError:
                if DEFAULT_LOCK.exists():
                    DEFAULT_LOCK.unlink()
            except Exception as exc:
                res.add("remover_lock", False, str(exc), "erro")
            else:
                res.add(
                    "remover_lock",
                    True,
                    f"arquivo residual removido ({age:.0f} min)",
                    "acao",
                )
                log_acao(
                    ciclo="pulse",
                    modo=modo,
                    acao="remover_lock",
                    detalhe=f"age_min={age:.0f} residual",
                )
                if _alerta_permitido("lock"):
                    _notificar(
                        "Lock residual removido",
                        f"processamento.lock órfão com {age:.0f} min (não estava em uso).",
                    )
                    _marcar_alerta("lock")

    # Contadores
    with database.connect() as c:
        pend = c.execute(
            "SELECT COUNT(*) FROM edicoes WHERE ocr_processado=0"
        ).fetchone()[0]
        jobs_r = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='rodando'"
        ).fetchone()[0]
        pubs = c.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
    res.add("estado", True, f"pend={pend} jobs_rodando={jobs_r} pubs={pubs}")

    # BOT parado com fila alta
    try:
        st = database.get_status_automacao()
        if not st.get("bot_vivo") and pend > 100:
            if _alerta_permitido("bot_parado"):
                _notificar(
                    "BOT parado com fila alta",
                    (
                        f"pendentes={pend}.\n"
                        "Como ligar:\n"
                        "1) iniciar.bat → [1] Web+BOT ou [3] só BOT\n"
                        "2) ou: python main.py\n"
                        "3) Admin (senha) → Agente → Ligar + modo formiga\n"
                        f"OCR agente hoje: {_ocr_calls_hoje()}/{SETTINGS.agente_max_ocr_por_dia or '∞'}"
                    ),
                )
                _marcar_alerta("bot_parado")
                res.add("alerta_bot", True, "BOT parado + fila alta", "warn")
    except Exception:
        pass

    # Heartbeat do agente
    database.set_setting(_K_PULSE, _now().isoformat(timespec="seconds"))
    database.set_setting("agente_heartbeat", _now().isoformat(timespec="seconds"))
    return res


def _cleanup_jobs_minutos(max_minutos: int) -> int:
    with database.connect() as c:
        cur = c.execute(
            """
            UPDATE jobs
            SET status = 'erro',
                mensagem = COALESCE(mensagem,'') || ' [agente: travado]',
                finalizado_em = CURRENT_TIMESTAMP,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE status = 'rodando'
              AND atualizado_em < datetime('now', ?)
            """,
            (f"-{max(1, int(max_minutos))} minutes",),
        )
        return int(cur.rowcount or 0)


# ---------------------------------------------------------------------------
# CÉREBRO
# ---------------------------------------------------------------------------

def _score_edicao(row: dict) -> int:
    score = int(row.get("score_prioridade") or 0) * 10
    score += int(row.get("score_candidatura") or 0)
    data = row.get("data_publicacao") or ""
    try:
        d = date.fromisoformat(data[:10])
        dias = (date.today() - d).days
        if dias <= 7:
            score += 50
        elif dias <= 30:
            score += 25
        elif dias <= 120:
            score += 10
    except Exception:
        pass
    titulo = (row.get("titulo") or "").casefold()
    if "inaj" in titulo or "inava" in titulo:
        score += 40
    caminho = row.get("caminho_local") or ""
    if caminho and Path(caminho).exists():
        score += 15
    # Prefere edições com cache OCR (redetecção barata se já processou parcialmente)
    if caminho:
        p = Path(caminho)
        if p.with_name(p.stem + ".ocr.json").exists():
            score += 5
    return score


def _ocr_calls_hoje() -> int:
    raw = database.get_setting(_K_OCR_DIA, "")
    if not raw or ":" not in raw:
        return 0
    bucket, n = raw.split(":", 1)
    if bucket != _now().strftime("%Y%m%d"):
        return 0
    try:
        return int(n)
    except ValueError:
        return 0


def _inc_ocr_calls(n: int = 1) -> None:
    hoje = _now().strftime("%Y%m%d")
    cur = _ocr_calls_hoje()
    database.set_setting(_K_OCR_DIA, f"{hoje}:{cur + n}")


def _pode_ocr(n: int = 1) -> bool:
    teto = int(SETTINGS.agente_max_ocr_por_dia or 0)
    if teto <= 0:
        return True
    return _ocr_calls_hoje() + n <= teto


def _hora_noturna() -> bool:
    h = _now().hour
    ini = int(SETTINGS.agente_noite_inicio or 22) % 24
    fim = int(SETTINGS.agente_noite_fim or 6) % 24
    if ini == fim:
        return False
    if ini < fim:
        return ini <= h < fim
    # ex.: 22–6
    return h >= ini or h < fim


def _max_ocr_ciclo(modo: str) -> int:
    base = max(0, int(SETTINGS.agente_max_ocr_por_ciclo or 1))
    if modo == "cirurgiao":
        base = min(base, 1)
    elif modo == "formiga":
        base = max(1, base)
    if _hora_noturna() and modo in {"formiga", "auto", "cirurgiao"}:
        mult = max(1, int(SETTINGS.agente_ocr_noite_mult or 1))
        base = max(base, base * mult if base else mult)
    # respeita orçamento diário restante
    teto_dia = int(SETTINGS.agente_max_ocr_por_dia or 0)
    if teto_dia > 0:
        resto = max(0, teto_dia - _ocr_calls_hoje())
        base = min(base, resto)
    return base


def _pick_pendentes(limit: int) -> list[dict]:
    desde = (SETTINGS.auto_process_desde or "").strip()
    sql = """
        SELECT id, titulo, data_publicacao, caminho_local,
               score_candidatura, score_prioridade, falhas_processamento
        FROM edicoes
        WHERE ocr_processado = 0
          AND COALESCE(falhas_processamento, 0) < ?
    """
    params: list[Any] = [max(1, SETTINGS.auto_process_max_falhas)]
    if desde:
        sql += " AND (data_publicacao IS NULL OR data_publicacao >= ?)"
        params.append(desde)
    sql += """
        ORDER BY
          COALESCE(score_prioridade, 1) DESC,
          COALESCE(score_candidatura, 0) DESC,
          data_publicacao DESC,
          id DESC
        LIMIT ?
    """
    params.append(max(limit * 8, 40))
    with database.connect() as c:
        rows = c.execute(sql, params).fetchall()
    ranked = sorted((dict(r) for r in rows), key=_score_edicao, reverse=True)
    return ranked[:limit]


def _pubs_fracas(limit: int) -> list[dict]:
    """Pubs sem resumo/valor; prioriza contratos/aditivos/extratos recentes."""
    with database.connect() as c:
        rows = c.execute(
            """
            SELECT p.id, p.edicao_id, p.tipo, p.numero, p.orgao, p.assunto,
                   p.valor, p.resumo_ia, p.trecho, p.pagina, p.importancia,
                   e.data_publicacao
            FROM publicacoes p
            JOIN edicoes e ON e.id = p.edicao_id
            WHERE p.resumo_ia IS NULL OR trim(p.resumo_ia) = ''
               OR p.valor IS NULL OR trim(p.valor) = ''
            ORDER BY e.data_publicacao DESC, p.id DESC
            LIMIT ?
            """,
            (max(limit * 4, 20),),
        ).fetchall()
    items = [dict(r) for r in rows]

    def prio(p: dict) -> tuple:
        tipo = (p.get("tipo") or "").casefold()
        bonus = 0
        for kw in ("contrato", "aditivo", "extrato", "pregão", "pregao", "licita"):
            if kw in tipo:
                bonus += 10
        if not (p.get("valor") or "").strip():
            bonus += 3
        if not (p.get("resumo_ia") or "").strip():
            bonus += 2
        return (-bonus, p.get("data_publicacao") or "")

    items.sort(key=prio)
    return items[:limit]


def run_cerebro(*, force: bool = False) -> CicloResultado:
    database.init_db()
    modo_cfg = modo_efetivo()
    modo = resolver_modo_auto() if modo_cfg == "auto" else modo_cfg
    res = CicloResultado(ciclo="cerebro", modo=modo)

    if not force and not agente_esta_ativo():
        res.add("skip", True, "agente desligado")
        return res

    if modo == "sentinela":
        res.add("skip", True, "modo sentinela — só observa")
        database.set_setting(_K_CEREBRO, _now().isoformat(timespec="seconds"))
        return res

    if modo == "escudo":
        res.add("skip", True, "modo escudo — cérebro não age (só pulse)")
        database.set_setting(_K_CEREBRO, _now().isoformat(timespec="seconds"))
        return res

    # OCR só se o lock estiver livre — evita espera de até 600s e OCR em duplicata
    # com BOT contínuo / webapp. (re-IA e subdetectado ainda podem rodar)
    skip_ocr_motivo = _motivo_pular_ocr_cerebro(force=force)
    if skip_ocr_motivo:
        res.add("skip_ocr", True, skip_ocr_motivo, "warn")
        log_acao(
            ciclo="cerebro",
            modo=modo,
            acao="skip_ocr",
            detalhe=skip_ocr_motivo,
            nivel="warn",
        )
        logger.info("[agente/cerebro] OCR adiado: %s", skip_ocr_motivo)
    else:
        max_ocr = _max_ocr_ciclo(modo)
        if not _pode_ocr(1) and modo in {"formiga", "cirurgiao", "auto"}:
            res.add(
                "skip_ocr",
                True,
                f"orçamento OCR diário esgotado ({_ocr_calls_hoje()}/{SETTINGS.agente_max_ocr_por_dia})",
                "warn",
            )
            max_ocr = 0

        if max_ocr > 0 and modo in {"formiga", "cirurgiao", "auto"}:
            picks = _pick_pendentes(max_ocr)
            if not picks:
                res.add("fila", True, "nenhuma pendente prioritária")
            for row in picks:
                eid = int(row["id"])
                if not _pode_ocr(1):
                    res.add("skip_ocr", True, "orçamento OCR diário esgotado", "warn")
                    break
                # Re-checa lock entre edições (BOT pode ter iniciado OCR)
                if is_lock_held():
                    det = f"lock adquirido por outro processo antes de id={eid}"
                    res.add("skip_ocr", True, det, "warn")
                    log_acao(
                        ciclo="cerebro",
                        modo=modo,
                        acao="skip_ocr",
                        detalhe=det,
                        nivel="warn",
                    )
                    break
                try:
                    from pipeline import processar_edicao_por_id

                    r = processar_edicao_por_id(
                        eid,
                        force_ocr=True,
                        fast_ocr=True,
                        notificar_se_encontrado=True,
                    )
                    _inc_ocr_calls(1)
                    if r is None:
                        res.add(
                            "ocr",
                            False,
                            f"id={eid} sem resultado",
                            "warn",
                        )
                    else:
                        det = (
                            f"id={eid} {row.get('data_publicacao')} "
                            f"inaja={r.encontrado} pubs={len(r.publicacoes)} "
                            f"ocr_dia={_ocr_calls_hoje()}"
                        )
                        res.add("ocr", True, det, "acao")
                        log_acao(ciclo="cerebro", modo=modo, acao="ocr", detalhe=det)
                except Exception as exc:
                    res.add("ocr", False, f"id={eid} {exc}", "erro")
                    log_acao(
                        ciclo="cerebro",
                        modo=modo,
                        acao="ocr",
                        detalhe=str(exc),
                        ok=False,
                        nivel="erro",
                    )

    # Re-IA (cirurgião / formiga / auto) — orçamento multi-camada (PR3)
    if modo in {"cirurgiao", "formiga", "auto"} and _pode_ia(1):
        limit = _limite_re_ia_ciclo()
        if limit <= 0:
            res.add(
                "re_ia",
                True,
                f"orçamento re-IA esgotado (hora={_ia_calls_hora()} dia={_re_ia_calls_hoje()})",
                "info",
            )
        else:
            try:
                from ai_processor import (
                    ia_disponivel,
                    refinar_publicacoes,
                    reset_ai_call_counter,
                )
                import qualidade

                if ia_disponivel():
                    fracos = qualidade.listar_candidatas_re_ia(limit)
                    if fracos:
                        reset_ai_call_counter()  # batch cap; corte real em _pode_chamar_ia
                        refinadas, stats = refinar_publicacoes(fracos[:limit])
                        n_ok = 0
                        for p in refinadas or []:
                            if not p or not p.get("id"):
                                continue
                            try:
                                data_ed = p.get("data_publicacao")
                                if not data_ed and p.get("edicao_id"):
                                    with database.connect() as c:
                                        row = c.execute(
                                            "SELECT data_publicacao FROM edicoes WHERE id=?",
                                            (p["edicao_id"],),
                                        ).fetchone()
                                        data_ed = row["data_publicacao"] if row else None
                                p = qualidade.aplicar_pos_re_ia(p, data_edicao=data_ed)
                                database.update_publicacao_ia(
                                    p, registrar_tentativa=True
                                )
                                _inc_ia_calls(1)
                                _inc_re_ia_dia(1)
                                n_ok += 1
                            except Exception:
                                logger.debug(
                                    "re_ia update falhou id=%s", p.get("id"), exc_info=True
                                )
                        det = (
                            f"re_ia n={n_ok}/{limit} "
                            f"dia={_re_ia_calls_hoje()} stats={stats}"
                        )
                        res.add("re_ia", True, det, "acao")
                        log_acao(ciclo="cerebro", modo=modo, acao="re_ia", detalhe=det)
                    else:
                        res.add("re_ia", True, "nenhuma candidata", "info")
                else:
                    res.add("re_ia", False, "IA indisponível", "warn")
            except Exception as exc:
                res.add("re_ia", False, str(exc), "erro")

    # Subdetectados leves (só cirurgião, 1 edição via cache)
    if modo == "cirurgiao":
        try:
            cands = [dict(r) for r in database.listar_edicoes_so_mencao(limit=3)]
            if cands:
                eid = int(cands[0]["id"])
                from pipeline import reprocessar_deteccao_de_cache

                r = reprocessar_deteccao_de_cache(eid, notificar_se_encontrado=False)
                det = f"id={eid} pubs={len(r.publicacoes) if r else 0}"
                res.add("subdetectado", True, det, "acao")
                log_acao(ciclo="cerebro", modo=modo, acao="subdetectado", detalhe=det)
        except Exception as exc:
            res.add("subdetectado", False, str(exc), "erro")

    # Gaps (PR4): scan leve formiga/cirurgiao; reprocess só se flag + cirurgião
    if modo in {"formiga", "cirurgiao", "auto"} and bool(
        getattr(SETTINGS, "quality_gap_detect", True)
    ):
        try:
            import qualidade

            max_gap = int(getattr(SETTINGS, "agente_max_gap_por_ciclo", 1) or 1)
            gaps = qualidade.listar_gaps_pendentes(max_gap)
            if gaps and bool(getattr(SETTINGS, "quality_gap_autoreprocess", False)):
                if modo == "cirurgiao" and not _motivo_pular_ocr_cerebro(force=force):
                    g0 = gaps[0]
                    eid = int(g0["id"])
                    acao = (g0.get("gap_acao") or "redetect_cache").casefold()
                    if acao == "force_ocr" and _pode_ocr(1):
                        from pipeline import processar_edicao_por_id

                        processar_edicao_por_id(
                            eid, force_ocr=True, fast_ocr=True, notificar_se_encontrado=False
                        )
                        _inc_ocr_calls(1)
                        res.add("gap_reprocess", True, f"force_ocr id={eid}", "acao")
                    else:
                        from pipeline import reprocessar_deteccao_de_cache

                        reprocessar_deteccao_de_cache(eid, notificar_se_encontrado=False)
                        res.add("gap_reprocess", True, f"redetect id={eid}", "acao")
                    with database.connect() as c:
                        c.execute(
                            "UPDATE edicoes SET gap_status='processado' WHERE id=?",
                            (eid,),
                        )
            elif gaps:
                res.add(
                    "gap_scan",
                    True,
                    f"pendentes={len(gaps)} (autoreprocess off)",
                    "info",
                )
        except Exception as exc:
            res.add("gap", False, str(exc)[:120], "warn")

    # Digest qualidade 1×/dia (formiga/cirurgiao/auto)
    if modo in {"formiga", "cirurgiao", "auto"} and bool(
        getattr(SETTINGS, "quality_digest_diario", True)
    ):
        hoje = _now().strftime("%Y%m%d")
        ult = database.get_setting(_K_DIGEST_DIA, "") or ""
        if ult != hoje:
            try:
                import qualidade

                path = qualidade.gravar_digest_qualidade()
                database.set_setting(_K_DIGEST_DIA, hoje)
                res.add("digest_qualidade", True, str(path or "ok"), "info")
            except Exception as exc:
                res.add("digest_qualidade", False, str(exc)[:80], "warn")

    database.set_setting(_K_CEREBRO, _now().isoformat(timespec="seconds"))
    return res


def deve_rodar_pulse() -> bool:
    if not agente_esta_ativo():
        return False
    last = _parse_iso(database.get_setting(_K_PULSE, ""))
    if not last:
        return True
    return (_now() - last).total_seconds() >= SETTINGS.agente_pulse_segundos


def deve_rodar_cerebro() -> bool:
    if not agente_esta_ativo():
        return False
    last = _parse_iso(database.get_setting(_K_CEREBRO, ""))
    if not last:
        return True
    return (_now() - last).total_seconds() >= SETTINGS.agente_cerebro_minutos * 60


def _motivo_pular_ocr_cerebro(*, force: bool = False) -> str:
    """Se OCR do cérebro deve ser adiado, retorna motivo; senão string vazia.

    ``force=True`` (admin/once) ainda respeita lock OS em uso — evita hang de 600s.
    Cede prioridade ao BOT contínuo quando ele está vivo e tem fila na janela.
    """
    try:
        if is_lock_held():
            return f"lock em uso ({_lock_desc()})"
    except Exception as exc:
        # Probe falhou: se arquivo é muito recente, seja conservador
        age = _lock_age_minutes()
        if age is not None and age < 5:
            return f"lock incerto (probe falhou: {exc}; idade≈{age:.0f}min)"

    # BOT contínuo na mesma máquina: não compete pela mesma fila de pendentes
    if force:
        return ""
    try:
        if not (SETTINGS.auto_process and SETTINGS.auto_process_continuo):
            return ""
        st = database.get_status_automacao()
        if not st.get("bot_vivo"):
            return ""
        fila = int(st.get("fila_proximo_ciclo") or 0)
        if fila > 0:
            return (
                f"BOT contínuo vivo com fila={fila} — cede prioridade "
                "(evita OCR duplicado)"
            )
    except Exception:
        pass
    return ""


def tick_from_bot() -> None:
    """Chamado no idle do main.py — não bloqueia muito se não for hora."""
    if not SETTINGS.agente_no_bot or not agente_esta_ativo():
        return
    try:
        if deve_rodar_pulse():
            r = run_pulse()
            warns = [a for a in r.acoes if a.nivel in {"warn", "erro", "acao"}]
            if warns:
                logger.info(
                    "agente pulse (%s): %s",
                    r.modo,
                    "; ".join(f"{a.acao}={a.detalhe}" for a in warns[:5]),
                )
            else:
                logger.debug("agente pulse: %s ações", len(r.acoes))
        if deve_rodar_cerebro():
            # Atalho barato: se lock held, nem entra no cérebro caro.
            # Não avança _K_CEREBRO → retenta no próximo tick (~pulse).
            try:
                held = is_lock_held()
            except Exception:
                held = False
            if held:
                logger.info(
                    "agente cérebro adiado no idle do BOT — %s",
                    _lock_desc(),
                )
                try:
                    log_acao(
                        ciclo="cerebro",
                        modo=modo_efetivo(),
                        acao="skip_lock",
                        detalhe=_lock_desc(),
                        nivel="warn",
                    )
                except Exception:
                    pass
            else:
                r = run_cerebro()
                logger.info(
                    "agente cérebro (%s): %s",
                    r.modo,
                    "; ".join(f"{a.acao}={a.detalhe}" for a in r.acoes[:5]),
                )
    except Exception:
        logger.exception("agente tick falhou")


def format_ciclo(res: CicloResultado) -> str:
    lines = [
        f"  ciclo={res.ciclo}  modo={res.modo}  em={res.started}",
        f"  ações: {len(res.acoes)}",
    ]
    for a in res.acoes:
        mark = "OK" if a.ok else "ER"
        lines.append(f"    [{mark}/{a.nivel}] {a.acao}: {a.detalhe}")
    return "\n".join(lines)


def loop_daemon(once: bool = False) -> None:
    """Loop standalone (scripts/_agente.py)."""
    database.init_db()
    logger.info(
        "Agente daemon · ativo=%s · modo=%s · pulse=%ss · cérebro=%smin",
        agente_esta_ativo(),
        modo_efetivo(),
        SETTINGS.agente_pulse_segundos,
        SETTINGS.agente_cerebro_minutos,
    )
    while True:
        try:
            if agente_esta_ativo():
                if deve_rodar_pulse() or once:
                    r = run_pulse(force=True)
                    print(format_ciclo(r))
                if deve_rodar_cerebro() or once:
                    # Em loop contínuo, se lock held: não marca cérebro como "feito"
                    # (evita adiar 30min o OCR). Em --once sempre roda (re-IA etc.).
                    held = False
                    if not once:
                        try:
                            held = is_lock_held()
                        except Exception:
                            held = False
                    if held:
                        logger.info(
                            "agente daemon: cérebro adiado — %s",
                            _lock_desc(),
                        )
                        print(f"  cérebro adiado (lock): {_lock_desc()}")
                    else:
                        r = run_cerebro(force=True)
                        print(format_ciclo(r))
            else:
                logger.debug("agente desligado — aguardando")
        except KeyboardInterrupt:
            print("Agente encerrado.")
            return
        except Exception:
            logger.exception("erro no loop do agente")
        if once:
            return
        # dorme o menor intervalo (pulse)
        time.sleep(max(15, min(60, SETTINGS.agente_pulse_segundos // 2)))
