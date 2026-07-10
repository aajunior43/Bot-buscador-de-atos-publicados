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
from process_lock import DEFAULT_LOCK

logger = logging.getLogger(__name__)

MODOS = ("escudo", "formiga", "cirurgiao", "sentinela", "auto")

# settings keys
_K_MODO = "agente_modo"
_K_ATIVO = "agente_ativo"
_K_PULSE = "agente_ultimo_pulse"
_K_CEREBRO = "agente_ultimo_cerebro"
_K_IA_HORA = "agente_ia_hora"  # "YYYYMMDDHH:count"
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
        "max_ia_hora": SETTINGS.agente_max_ia_por_hora,
        "no_bot": SETTINGS.agente_no_bot,
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
        from notifier import enviar_teste  # type: ignore

        # Preferir arquivo/telegram genérico
        texto = f"🤖 AGENTE: {titulo}\n{corpo}"
        # usar canal de alerta de arquivo direto
        alert_dir = SETTINGS.alert_dir
        alert_dir.mkdir(parents=True, exist_ok=True)
        path = alert_dir / f"{date.today().isoformat()}.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n{texto}\n")
        # Telegram se configurado
        if SETTINGS.telegram_bot_token and SETTINGS.telegram_chat_id:
            try:
                import requests

                requests.post(
                    f"https://api.telegram.org/bot{SETTINGS.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": SETTINGS.telegram_chat_id,
                        "text": texto[:4000],
                    },
                    timeout=15,
                )
            except Exception:
                logger.debug("telegram agente falhou", exc_info=True)
    except Exception:
        logger.debug("notificar agente falhou", exc_info=True)


def _lock_age_minutes() -> float | None:
    if not DEFAULT_LOCK.exists():
        return None
    try:
        mtime = DEFAULT_LOCK.stat().st_mtime
        return max(0.0, (time.time() - mtime) / 60.0)
    except OSError:
        return 0.0


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

    # Lock velho
    age = _lock_age_minutes()
    if age is not None:
        res.add("lock_presente", True, f"idade≈{age:.0f} min", "warn")
        if (
            SETTINGS.agente_auto_limpar_lock
            and not only_observe
            and age >= SETTINGS.agente_lock_max_minutos
        ):
            try:
                DEFAULT_LOCK.unlink(missing_ok=True)  # type: ignore[call-arg]
                # py3.8 compat
            except TypeError:
                if DEFAULT_LOCK.exists():
                    DEFAULT_LOCK.unlink()
            except Exception as exc:
                res.add("remover_lock", False, str(exc), "erro")
            else:
                res.add("remover_lock", True, f"lock morto removido ({age:.0f} min)", "acao")
                log_acao(
                    ciclo="pulse",
                    modo=modo,
                    acao="remover_lock",
                    detalhe=f"age_min={age:.0f}",
                )
                if _alerta_permitido("lock"):
                    _notificar("Lock removido", f"processamento.lock com {age:.0f} min sem update.")
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
                    f"pendentes={pend}. Considere iniciar [1] ou [3].",
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
    return score


def _pick_pendentes(limit: int) -> list[dict]:
    with database.connect() as c:
        rows = c.execute(
            """
            SELECT id, titulo, data_publicacao, caminho_local,
                   score_candidatura, score_prioridade, falhas_processamento
            FROM edicoes
            WHERE ocr_processado = 0
              AND COALESCE(falhas_processamento, 0) < ?
            ORDER BY
              COALESCE(score_prioridade, 1) DESC,
              COALESCE(score_candidatura, 0) DESC,
              data_publicacao DESC,
              id DESC
            LIMIT ?
            """,
            (max(1, SETTINGS.auto_process_max_falhas), max(limit * 5, 20)),
        ).fetchall()
    ranked = sorted((dict(r) for r in rows), key=_score_edicao, reverse=True)
    return ranked[:limit]


def _pubs_fracas(limit: int) -> list[dict]:
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
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


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

    # Lock ocupado → não OCR
    if DEFAULT_LOCK.exists() and (_lock_age_minutes() or 0) < 5:
        res.add("skip_ocr", True, "lock ativo (outro processo)", "warn")
        # ainda pode re-IA
    else:
        max_ocr = SETTINGS.agente_max_ocr_por_ciclo
        if modo == "formiga":
            max_ocr = max(1, max_ocr)
        elif modo == "cirurgiao":
            max_ocr = min(max_ocr, 1)  # cirurgião prioriza qualidade

        if max_ocr > 0 and modo in {"formiga", "cirurgiao", "auto"}:
            picks = _pick_pendentes(max_ocr)
            if not picks:
                res.add("fila", True, "nenhuma pendente prioritária")
            for row in picks:
                eid = int(row["id"])
                try:
                    from pipeline import processar_edicao_por_id

                    r = processar_edicao_por_id(
                        eid,
                        force_ocr=True,
                        fast_ocr=True,
                        notificar_se_encontrado=True,
                    )
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
                            f"inaja={r.encontrado} pubs={len(r.publicacoes)}"
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

    # Re-IA (cirurgião / auto / formiga se sobrar orçamento)
    if modo in {"cirurgiao", "formiga", "auto"} and _pode_ia(1):
        fracos = _pubs_fracas(min(5, SETTINGS.agente_max_ia_por_hora - _ia_calls_hora()))
        if fracos:
            try:
                from ai_processor import ia_disponivel, refinar_publicacoes, reset_ai_call_counter

                if ia_disponivel():
                    pubs = []
                    for r in fracos:
                        trecho = (r.get("trecho") or r.get("assunto") or "").strip()
                        if not trecho:
                            continue
                        pubs.append({**r, "trecho": trecho})
                    if pubs:
                        reset_ai_call_counter()
                        refinadas, stats = refinar_publicacoes(pubs[:3])
                        _inc_ia_calls(len(pubs[:3]))
                        n_ok = 0
                        for p in refinadas or []:
                            if not p or not p.get("id"):
                                continue
                            try:
                                database.update_publicacao_ia(p)
                                n_ok += 1
                            except Exception:
                                pass
                        det = f"re_ia n={n_ok} stats={stats}"
                        res.add("re_ia", True, det, "acao")
                        log_acao(ciclo="cerebro", modo=modo, acao="re_ia", detalhe=det)
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
            res.add("subdetectado", False, str(exc)[:120], "warn")


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


def tick_from_bot() -> None:
    """Chamado no idle do main.py — não bloqueia muito se não for hora."""
    if not SETTINGS.agente_no_bot or not agente_esta_ativo():
        return
    try:
        if deve_rodar_pulse():
            r = run_pulse()
            logger.debug("agente pulse: %s ações", len(r.acoes))
        if deve_rodar_cerebro():
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
