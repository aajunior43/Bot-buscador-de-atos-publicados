"""Terminal rico para observar o BOT em tempo real.

- Cores ANSI (Windows 10+ / terminais modernos)
- Banners, cockpit, barras com ETA, lista de atos
- Marcos da sessão, histórico mini, throughput
- Formatter de logging colorido (arquivo permanece plain)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── ANSI ────────────────────────────────────────────────────────────────────
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_RED = "\033[41m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_DARK = "\033[48;5;236m"


_NO_COLOR = bool(os.getenv("NO_COLOR", "").strip())
_FORCE_COLOR = bool(os.getenv("FORCE_COLOR", "").strip()) or bool(
    os.getenv("RICH_TERMINAL", "1").strip() not in {"0", "false", "no"}
)
# Com iniciar_tudo o stdout do BOT é pipe (não TTY) — forçamos cor por padrão
_ENABLED = (sys.stdout.isatty() or _FORCE_COLOR) and not _NO_COLOR
_WIDTH = 74
_lock = threading.Lock()
_last_progress_line = ""
_progress_t0: float | None = None
_progress_label = ""


def enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _c(code: str, text: str) -> str:
    if not _ENABLED:
        return text
    return f"{code}{text}{C.RESET}"


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _line(char: str = "─", width: int = _WIDTH) -> str:
    return char * width


def _fmt_dur(secs: float) -> str:
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{secs:.0f}s" if secs >= 10 else f"{secs:.1f}s"
    m, s = divmod(int(secs), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def bar(current: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "░" * width
    pct_v = max(0.0, min(1.0, current / total))
    filled = int(round(pct_v * width))
    # Gradiente de blocos no fim do preenchimento
    if filled <= 0:
        return "░" * width
    if filled >= width:
        return "█" * width
    return "█" * (filled - 1) + "▓" + "░" * (width - filled)


def bar_color(current: int, total: int) -> str:
    p = pct(current, total)
    if p >= 100:
        return C.BRIGHT_GREEN
    if p >= 66:
        return C.BRIGHT_CYAN
    if p >= 33:
        return C.BRIGHT_BLUE
    return C.MAGENTA


def pct(current: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round(100 * current / total))


def _emit(text: str = "", *, end: str = "\n", flush: bool = True) -> None:
    with _lock:
        sys.stdout.write(text + end)
        if flush:
            sys.stdout.flush()


def rule(title: str = "", color: str = C.BRIGHT_CYAN) -> None:
    if title:
        pad = max(0, _WIDTH - len(title) - 4)
        left = pad // 2
        right = pad - left
        body = f"{'─' * left} {title} {'─' * right}"
        _emit(_c(color + C.BOLD, body[:_WIDTH]))
    else:
        _emit(_c(C.DIM, _line()))


def box(lines: list[str], *, color: str = C.BRIGHT_CYAN, title: str = "") -> None:
    """Caixa Unicode simples."""
    inner_w = _WIDTH - 4
    top = f"╭{'─' * (_WIDTH - 2)}╮"
    bot = f"╰{'─' * (_WIDTH - 2)}╯"
    _emit(_c(color, top))
    if title:
        t = f" {title} "
        mid = f"│{_c(C.BOLD + color, t.ljust(inner_w + 2)[: inner_w + 2])}│"
        # simpler:
        _emit(_c(color, "│") + _c(C.BOLD + C.BRIGHT_WHITE, f" {title}".ljust(_WIDTH - 2)[: _WIDTH - 2]) + _c(color, "│"))
    for line in lines:
        plain = line
        # pad roughly by visible length without ANSI (good enough)
        visible = plain
        for code in (
            C.RESET, C.BOLD, C.DIM, C.BRIGHT_GREEN, C.BRIGHT_CYAN, C.BRIGHT_YELLOW,
            C.BRIGHT_RED, C.BRIGHT_MAGENTA, C.BRIGHT_WHITE, C.BRIGHT_BLUE, C.DIM,
            C.CYAN, C.GREEN, C.YELLOW, C.RED, C.MAGENTA, C.WHITE, C.BLUE,
        ):
            visible = visible.replace(code, "")
        pad = max(0, _WIDTH - 3 - len(visible))
        _emit(_c(color, "│ ") + plain + (" " * pad) + _c(color, "│"))
    _emit(_c(color, bot))


def banner_startup(
    *,
    interval_h: int,
    continuo: bool,
    lote: int,
    max_ciclo: int,
    dias: int,
    desde: str,
    max_falhas: int,
) -> None:
    enable_windows_ansi()
    _emit()
    box(
        [
            _c(C.BOLD + C.BRIGHT_WHITE, "Rastreador de atos · O Regional · Inajá-PR"),
            _c(C.DIM, f"{_now()}  ·  terminal em tempo real  ·  modo espectador 🔥"),
            "",
            f"{_c(C.CYAN, '▸')} ciclo a cada {_c(C.BRIGHT_WHITE, str(interval_h) + 'h')}"
            f"   {_c(C.CYAN, '▸')} fila contínua {_c(C.BRIGHT_GREEN, 'SIM' if continuo else 'não')}",
            f"{_c(C.CYAN, '▸')} lote {_c(C.BRIGHT_WHITE, str(lote))}"
            f" / máx {_c(C.BRIGHT_WHITE, str(max_ciclo))}"
            f"   {_c(C.CYAN, '▸')} desde {_c(C.BRIGHT_WHITE, desde or '—')}",
            f"{_c(C.CYAN, '▸')} janela {_c(C.BRIGHT_WHITE, str(dias) if dias else '∞')}"
            f"   {_c(C.CYAN, '▸')} quarentena após {_c(C.BRIGHT_YELLOW, str(max_falhas))} falhas",
            "",
            _c(C.DIM, "Acompanhe: barras OCR · atos · ETA · marcos da sessão"),
        ],
        color=C.BRIGHT_MAGENTA,
        title="MONITOR INAJÁ · BOT",
    )
    _emit()


@dataclass
class SessionStats:
    started_at: float = field(default_factory=time.time)
    processadas: int = 0
    com_inaja: int = 0
    publicacoes: int = 0
    falhas: int = 0
    cache_hits: int = 0
    segundos_ocr: float = 0.0
    ultima_edicao: str = ""
    # 'I' = Inajá, '.' = ok sem Inajá, 'x' = falha
    historico: deque[str] = field(default_factory=lambda: deque(maxlen=40))
    duracoes: deque[float] = field(default_factory=lambda: deque(maxlen=20))
    marcos_feitos: set[int] = field(default_factory=set)
    primeiro_inaja: bool = False

    def elapsed(self) -> str:
        return _fmt_dur(time.time() - self.started_at)

    def media_seg(self) -> float | None:
        if not self.duracoes:
            return None
        return sum(self.duracoes) / len(self.duracoes)

    def throughput_h(self) -> float | None:
        elapsed = time.time() - self.started_at
        if elapsed < 30 or self.processadas <= 0:
            return None
        return self.processadas / (elapsed / 3600)

    def eta_fila(self, pendentes: int | None) -> str | None:
        media = self.media_seg()
        if media is None or not pendentes or pendentes <= 0:
            return None
        return _fmt_dur(media * pendentes)

    def spark(self) -> str:
        if not self.historico:
            return "—"
        m = {"I": "█", ".": "▒", "x": "░"}
        return "".join(m.get(c, "?") for c in self.historico)

    def spark_colored(self) -> str:
        if not self.historico:
            return _c(C.DIM, "—")
        parts = []
        for ch in self.historico:
            if ch == "I":
                parts.append(_c(C.BRIGHT_GREEN, "█"))
            elif ch == ".":
                parts.append(_c(C.DIM, "▒"))
            elif ch == "x":
                parts.append(_c(C.BRIGHT_RED, "░"))
            else:
                parts.append("?")
        return "".join(parts)

    def hit_rate(self) -> float | None:
        if self.processadas <= 0:
            return None
        return 100.0 * self.com_inaja / self.processadas

    def score(self) -> int:
        """Pontuação divertida da sessão (só para o terminal)."""
        return (
            self.processadas * 10
            + self.com_inaja * 50
            + self.publicacoes * 15
            - self.falhas * 20
        )


SESSION = SessionStats()

# Pipeline visual da edição atual
_PHASES = ("DL", "OCR", "DET", "IA", "ALR")
_PHASE_LABEL = {
    "DL": "Download",
    "OCR": "OCR",
    "DET": "Detecção",
    "IA": "IA",
    "ALR": "Alerta",
}
_phase_state: dict[str, str] = {p: "wait" for p in _PHASES}
# wait | run | ok | skip | fail

# Placar das últimas edições: {titulo, dur, flag, pubs}
_scoreboard: deque[dict[str, Any]] = deque(maxlen=8)


def phase_reset() -> None:
    global _phase_state
    _phase_state = {p: "wait" for p in _PHASES}


def phase_set(phase: str, state: str) -> None:
    """Atualiza etapa e redesenha o trilho  DL●──OCR○──…"""
    if phase not in _phase_state:
        return
    _phase_state[phase] = state
    # Se começou OCR, download ok implícito se ainda wait
    if phase == "OCR" and state == "run" and _phase_state["DL"] == "wait":
        _phase_state["DL"] = "ok"
    _draw_phase_rail()


def _draw_phase_rail() -> None:
    parts: list[str] = []
    for i, p in enumerate(_PHASES):
        st = _phase_state[p]
        if st == "ok":
            node = _c(C.BRIGHT_GREEN + C.BOLD, f"●{p}")
        elif st == "run":
            node = _c(C.BRIGHT_CYAN + C.BOLD, f"◉{p}")
        elif st == "fail":
            node = _c(C.BRIGHT_RED + C.BOLD, f"✗{p}")
        elif st == "skip":
            node = _c(C.DIM, f"○{p}")
        else:
            node = _c(C.DIM, f"○{p}")
        parts.append(node)
        if i < len(_PHASES) - 1:
            nxt = _phase_state[_PHASES[i + 1]]
            if st == "ok":
                link = _c(C.BRIGHT_GREEN, "──")
            elif st == "run" or nxt == "run":
                link = _c(C.BRIGHT_CYAN, "──")
            else:
                link = _c(C.DIM, "╌╌")
            parts.append(link)
    _emit("  " + "".join(parts))
    # legenda curta da etapa em execução
    running = [p for p, s in _phase_state.items() if s == "run"]
    if running:
        p = running[0]
        _emit(_c(C.DIM, f"     agora: {_PHASE_LABEL.get(p, p)}"))


def _maybe_milestone() -> None:
    n = SESSION.processadas
    for mark in (5, 10, 25, 50, 100, 200, 500):
        if n == mark and mark not in SESSION.marcos_feitos:
            SESSION.marcos_feitos.add(mark)
            _emit()
            _emit(
                _c(
                    C.BRIGHT_YELLOW + C.BOLD,
                    f"  ★  MARCO  ·  {mark} edições processadas nesta sessão  ·  "
                    f"{SESSION.com_inaja} com Inajá  ·  {SESSION.elapsed()}",
                )
            )
            _emit(
                _c(
                    C.DIM,
                    f"  histórico  {SESSION.spark()}   "
                    f"(█ Inajá  ▒ ok  ░ falha)",
                )
            )
            _emit()


def status_fila(
    *,
    pendentes: int | None = None,
    fila: int | None = None,
    quarentena: int | None = None,
    quiet: bool = False,
) -> None:
    if quiet:
        return
    bits = [
        f"⏱ {SESSION.elapsed()}",
        f"✓ {SESSION.processadas}",
        f"🏛 {SESSION.com_inaja}",
        f"📄 {SESSION.publicacoes}",
    ]
    if SESSION.falhas:
        bits.append(f"⚠ {SESSION.falhas}")
    thr = SESSION.throughput_h()
    if thr is not None:
        bits.append(f"⚡ {thr:.1f}/h")
    media = SESSION.media_seg()
    if media is not None:
        bits.append(f"⌀ {_fmt_dur(media)}/ed")
    if pendentes is not None:
        bits.append(f"📋 {pendentes}")
        eta = SESSION.eta_fila(pendentes)
        if eta:
            bits.append(f"⏳ ~{eta}")
    if fila is not None:
        bits.append(f"⏭ {fila}")
    if quarentena:
        bits.append(f"🚫 {quarentena}")
    _emit(_c(C.DIM, f"  [{_now()}] " + "  ·  ".join(bits)))
    if SESSION.historico:
        _emit(_c(C.DIM, f"           trilha  {SESSION.spark()}"))


def cockpit(
    *,
    pendentes: int | None = None,
    fila: int | None = None,
    quarentena: int | None = None,
    bot_vivo: bool | None = None,
) -> None:
    """Painel compacto quando o BOT está ocioso ou entre lotes."""
    thr = SESSION.throughput_h()
    media = SESSION.media_seg()
    lines = [
        f"{_c(C.BRIGHT_GREEN if bot_vivo else C.BRIGHT_YELLOW, '● BOT ' + ('online' if bot_vivo else '—'))}"
        f"   sessão {_c(C.BRIGHT_WHITE, SESSION.elapsed())}"
        + (f"   {_c(C.CYAN, f'{thr:.1f} ed/h')}" if thr else ""),
        f"processadas {_c(C.BRIGHT_WHITE, str(SESSION.processadas))}   "
        f"Inajá {_c(C.BRIGHT_GREEN, str(SESSION.com_inaja))}   "
        f"pubs {_c(C.BRIGHT_WHITE, str(SESSION.publicacoes))}   "
        f"falhas {_c(C.BRIGHT_RED if SESSION.falhas else C.DIM, str(SESSION.falhas))}",
    ]
    if pendentes is not None or fila is not None:
        eta = SESSION.eta_fila(pendentes) if pendentes else None
        lines.append(
            f"pendentes {_c(C.BRIGHT_YELLOW, str(pendentes if pendentes is not None else '—'))}   "
            f"fila {_c(C.BRIGHT_WHITE, str(fila if fila is not None else '—'))}   "
            f"quarentena {_c(C.BRIGHT_YELLOW, str(quarentena or 0))}"
            + (f"   ETA ~{_c(C.BRIGHT_CYAN, eta)}" if eta else "")
        )
    hr = SESSION.hit_rate()
    if hr is not None:
        lines.append(
            f"taxa Inajá {_c(C.BRIGHT_GREEN, f'{hr:.0f}%')}   "
            f"score {_c(C.BRIGHT_YELLOW, str(SESSION.score()))}"
        )
    if media is not None:
        lines.append(
            _c(C.DIM, f"média/edição {_fmt_dur(media)}   ")
            + "trilha "
            + SESSION.spark_colored()
        )
    elif SESSION.historico:
        lines.append("trilha " + SESSION.spark_colored())
    box(lines, color=C.BRIGHT_CYAN, title="COCKPIT")
    if _scoreboard and len(_scoreboard) >= 2:
        show_scoreboard()


def edition_start(
    *,
    titulo: str,
    data: str | None,
    edicao_id: int | None,
    indice: int | None = None,
    total_lote: int | None = None,
    pendentes_restantes: int | None = None,
) -> float:
    global _last_progress_line, _progress_t0, _progress_label
    _last_progress_line = ""
    _progress_t0 = None
    _progress_label = ""
    phase_reset()
    t0 = time.time()
    SESSION.ultima_edicao = titulo or (f"id={edicao_id}" if edicao_id else "?")
    _emit()
    rule("NOVA EDIÇÃO", C.BRIGHT_BLUE)
    lote = ""
    if indice and total_lote:
        lote = f"  {_c(C.BG_BLUE + C.BRIGHT_WHITE + C.BOLD, f' {indice}/{total_lote} ')}"
        # mini barra do lote
        lote += f"  {_c(C.DIM, bar(indice, total_lote, 12))} {_c(C.DIM, f'{pct(indice, total_lote)}%')}"
    _emit(f"  {_c(C.BOLD + C.BRIGHT_WHITE, titulo or 'Sem título')}{lote}")
    meta = []
    if data:
        meta.append(f"📅 {data}")
    if edicao_id:
        meta.append(f"id={edicao_id}")
    if pendentes_restantes is not None:
        meta.append(f"restam ~{pendentes_restantes}")
        eta = SESSION.eta_fila(pendentes_restantes)
        if eta:
            meta.append(f"ETA fila ~{eta}")
    media = SESSION.media_seg()
    if media is not None:
        meta.append(f"⌀ {_fmt_dur(media)}")
    hr = SESSION.hit_rate()
    if hr is not None:
        meta.append(f"🎯 {hr:.0f}% Inajá")
    if meta:
        _emit(_c(C.DIM, "  " + "  ·  ".join(meta)))
    _emit(_c(C.DIM, f"  ▶ {_now()}"))
    _draw_phase_rail()
    return t0


def step(
    name: str,
    detail: str = "",
    *,
    ok: bool | None = None,
    phase: str | None = None,
) -> None:
    if phase:
        if ok is True:
            phase_set(phase, "ok")
        elif ok is False:
            phase_set(phase, "fail")
        else:
            phase_set(phase, "run")
    if ok is True:
        icon = _c(C.BRIGHT_GREEN, "✓")
    elif ok is False:
        icon = _c(C.BRIGHT_RED, "✗")
    else:
        icon = _c(C.BRIGHT_CYAN, "→")
    detail_s = f"  {_c(C.DIM, detail)}" if detail else ""
    _emit(f"  {icon} {_c(C.BOLD, name)}{detail_s}")


def progress(
    current: int,
    total: int,
    *,
    label: str = "OCR",
    extra: str = "",
) -> None:
    """Barra de progresso com % e ETA da etapa."""
    global _last_progress_line, _progress_t0, _progress_label
    if _progress_label != label or _progress_t0 is None:
        _progress_t0 = time.time()
        _progress_label = label

    p = pct(current, total)
    b = bar(current, total, 26)
    color = bar_color(current, total)
    extra_s = f"  {_c(C.DIM, extra)}" if extra else ""

    eta_s = ""
    if _progress_t0 and current > 0 and total > current:
        elapsed = time.time() - _progress_t0
        rate = current / elapsed if elapsed > 0 else 0
        if rate > 0:
            rem = (total - current) / rate
            eta_s = f"  {_c(C.YELLOW, 'ETA ' + _fmt_dur(rem))}"

    body = (
        f"  {_c(C.DIM, '│')} {_c(color, b)} "
        f"{_c(C.BOLD, f'{p:3d}%')}  "
        f"{_c(C.WHITE, f'{current:>3}/{total:<3}')}  "
        f"{_c(C.CYAN, label)}{eta_s}{extra_s}"
    )
    with _lock:
        if body != _last_progress_line:
            sys.stdout.write(body + "\n")
            sys.stdout.flush()
            _last_progress_line = body


def _parse_valor_brl(texto: str) -> float | None:
    import re

    if not texto:
        return None
    m = re.search(r"([\d.]+,\d{2})", texto.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def show_publicacoes(publicacoes: list[dict[str, Any]] | list[Any], *, max_items: int = 8) -> None:
    """Lista atos encontrados (estilo card)."""
    if not publicacoes:
        return
    total_valor = 0.0
    n_val = 0
    _emit(_c(C.BRIGHT_GREEN + C.BOLD, f"  ┌─ Atos de Inajá ({len(publicacoes)})"))
    for i, p in enumerate(publicacoes[:max_items], start=1):
        if hasattr(p, "keys"):
            d = dict(p)
        elif isinstance(p, dict):
            d = p
        else:
            d = {}
        tipo = (d.get("tipo") or "Ato").strip()
        num = (d.get("numero") or "").strip()
        orgao = (d.get("orgao") or "").strip()
        valor = (d.get("valor") or "").strip()
        resumo = (d.get("resumo_ia") or d.get("assunto") or "").strip()
        pagina = d.get("pagina")
        head = f"{tipo}" + (f" {num}" if num else "")
        _emit(
            f"  {_c(C.BRIGHT_GREEN, '│')} {_c(C.BOLD + C.BRIGHT_WHITE, f'{i}. {head}')}"
            + (f"  {_c(C.DIM, 'pág.' + str(pagina))}" if pagina else "")
        )
        if orgao:
            _emit(f"  {_c(C.BRIGHT_GREEN, '│')}    {_c(C.CYAN, orgao)}")
        if valor:
            _emit(f"  {_c(C.BRIGHT_GREEN, '│')}    {_c(C.BRIGHT_YELLOW, '💰 ' + valor)}")
            v = _parse_valor_brl(valor)
            if v is not None:
                total_valor += v
                n_val += 1
        if resumo:
            r = resumo.replace("\n", " ")
            if len(r) > 90:
                r = r[:87] + "…"
            _emit(f"  {_c(C.BRIGHT_GREEN, '│')}    {_c(C.DIM, r)}")
    if len(publicacoes) > max_items:
        _emit(
            f"  {_c(C.BRIGHT_GREEN, '│')}    "
            f"{_c(C.DIM, f'… +{len(publicacoes) - max_items} ato(s)')}"
        )
    # soma valores das pubs listadas + restantes se possível
    for p in publicacoes[max_items:]:
        d = dict(p) if isinstance(p, dict) or hasattr(p, "keys") else {}
        v = _parse_valor_brl(str(d.get("valor") or ""))
        if v is not None:
            total_valor += v
            n_val += 1
    if n_val:
        brl = f"R$ {total_valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        _emit(
            f"  {_c(C.BRIGHT_GREEN, '│')} "
            f"{_c(C.BRIGHT_YELLOW + C.BOLD, f'Σ valores ({n_val}): {brl}')}"
        )
    _emit(_c(C.BRIGHT_GREEN, "  └─"))


def edition_end(
    *,
    ok: bool,
    tem_inaja: bool = False,
    n_pubs: int = 0,
    n_mencoes: int = 0,
    t0: float | None = None,
    erro: str = "",
    from_cache: bool = False,
    publicacoes: list[Any] | None = None,
) -> None:
    secs = 0.0
    dur = ""
    if t0 is not None:
        secs = time.time() - t0
        SESSION.segundos_ocr += secs
        SESSION.duracoes.append(secs)
        dur = _fmt_dur(secs)

    titulo_curto = (SESSION.ultima_edicao or "?")[:36]
    if ok:
        SESSION.processadas += 1
        if tem_inaja:
            SESSION.com_inaja += 1
            SESSION.historico.append("I")
            flag = "I"
            if not SESSION.primeiro_inaja:
                SESSION.primeiro_inaja = True
                _emit(
                    _c(
                        C.BG_GREEN + C.BOLD + C.BLACK,
                        "  ★ PRIMEIRO INAJÁ DA SESSÃO ★  ",
                    )
                )
        else:
            SESSION.historico.append(".")
            flag = "."
        SESSION.publicacoes += n_pubs
        if from_cache:
            SESSION.cache_hits += 1

        # fecha trilho: se sem Inajá, alerta/IA skip
        if tem_inaja:
            if _phase_state.get("IA") == "wait":
                _phase_state["IA"] = "ok"
            if _phase_state.get("ALR") == "wait":
                _phase_state["ALR"] = "ok"
        else:
            if _phase_state.get("IA") == "wait":
                _phase_state["IA"] = "skip"
            if _phase_state.get("ALR") == "wait":
                _phase_state["ALR"] = "skip"
        _draw_phase_rail()

        if tem_inaja and publicacoes:
            show_publicacoes(publicacoes)

        if tem_inaja:
            badge = _c(C.BG_GREEN + C.BOLD + C.BLACK, " INAJÁ ")
            badge += f"  {_c(C.BRIGHT_GREEN + C.BOLD, f'{n_pubs} publicação(ões)')}"
        else:
            badge = _c(C.DIM, " sem Inajá")
            if n_mencoes:
                badge += f"  {_c(C.YELLOW, f'{n_mencoes} menção(ões)')}"

        _emit(f"  {_c(C.BRIGHT_GREEN + C.BOLD, '✓ CONCLUÍDA')}  {badge}")
        bits = []
        if dur:
            bits.append(f"⏱ {dur}")
        if from_cache:
            bits.append("💾 cache")
        thr = SESSION.throughput_h()
        bits.append(
            f"sessão {SESSION.processadas} ok · {SESSION.com_inaja} Inajá · {SESSION.publicacoes} pubs"
        )
        if thr:
            bits.append(f"{thr:.1f}/h")
        hr = SESSION.hit_rate()
        if hr is not None:
            bits.append(f"🎯 {hr:.0f}%")
        bits.append(f"pts {SESSION.score()}")
        _emit(_c(C.DIM, "  " + "  ·  ".join(bits)))
        _scoreboard.appendleft(
            {
                "titulo": titulo_curto,
                "dur": dur or "—",
                "flag": flag,
                "pubs": n_pubs,
            }
        )
        _maybe_milestone()
        if SESSION.processadas % 3 == 0:
            show_scoreboard()
    else:
        SESSION.falhas += 1
        SESSION.historico.append("x")
        # marca fase em run como fail
        for p, st in list(_phase_state.items()):
            if st == "run":
                _phase_state[p] = "fail"
        _draw_phase_rail()
        _emit(
            f"  {_c(C.BRIGHT_RED + C.BOLD, '✗ FALHOU')}"
            + (f"  {_c(C.RED, erro[:120])}" if erro else "")
        )
        if dur:
            _emit(_c(C.DIM, f"  ⏱ {dur}  ·  falhas sessão: {SESSION.falhas}"))
        _scoreboard.appendleft(
            {
                "titulo": titulo_curto,
                "dur": dur or "—",
                "flag": "x",
                "pubs": 0,
            }
        )
    rule(color=C.DIM)


def show_scoreboard() -> None:
    """Placar das últimas edições da sessão."""
    if not _scoreboard:
        return
    _emit(_c(C.BRIGHT_MAGENTA + C.BOLD, "  ┌─ Placar recente"))
    for i, row in enumerate(_scoreboard, start=1):
        flag = row.get("flag", ".")
        if flag == "I":
            mark = _c(C.BRIGHT_GREEN + C.BOLD, "INAJÁ")
        elif flag == "x":
            mark = _c(C.BRIGHT_RED, "FALHA")
        else:
            mark = _c(C.DIM, "ok   ")
        pubs = row.get("pubs") or 0
        pubs_s = f" · {pubs} pub" if pubs else ""
        _emit(
            f"  {_c(C.BRIGHT_MAGENTA, '│')} {i:>2}. {mark}  "
            f"{_c(C.BRIGHT_WHITE, str(row.get('titulo') or '—'))}  "
            f"{_c(C.DIM, str(row.get('dur') or ''))}{pubs_s}"
        )
    _emit(
        f"  {_c(C.BRIGHT_MAGENTA, '│')} "
        f"{_c(C.DIM, 'trilha ')}{SESSION.spark_colored()}"
    )
    _emit(_c(C.BRIGHT_MAGENTA, "  └─"))


def session_summary(*, reason: str = "sessão encerrada") -> None:
    """Resumo final (Ctrl+C ou fim do processo)."""
    thr = SESSION.throughput_h()
    hr = SESSION.hit_rate()
    lines = [
        _c(C.BRIGHT_WHITE + C.BOLD, reason),
        f"duração {_c(C.BRIGHT_CYAN, SESSION.elapsed())}   "
        f"pontos {_c(C.BRIGHT_YELLOW + C.BOLD, str(SESSION.score()))}",
        f"edições {_c(C.BRIGHT_WHITE, str(SESSION.processadas))}   "
        f"Inajá {_c(C.BRIGHT_GREEN, str(SESSION.com_inaja))}"
        + (f" ({hr:.0f}%)" if hr is not None else "")
        + f"   pubs {_c(C.BRIGHT_WHITE, str(SESSION.publicacoes))}   "
        f"falhas {_c(C.BRIGHT_RED if SESSION.falhas else C.DIM, str(SESSION.falhas))}",
    ]
    if thr:
        lines.append(f"ritmo {_c(C.CYAN, f'{thr:.1f} ed/h')}")
    if SESSION.historico:
        lines.append("trilha " + SESSION.spark_colored())
    if SESSION.ultima_edicao:
        lines.append(_c(C.DIM, f"última: {SESSION.ultima_edicao[:50]}"))
    box(lines, color=C.BRIGHT_MAGENTA, title="FIM DE SESSÃO")
    show_scoreboard()


def ciclo_banner(titulo: str, detalhe: str = "") -> None:
    _emit()
    rule(titulo, C.BRIGHT_YELLOW)
    if detalhe:
        _emit(_c(C.DIM, f"  {detalhe}"))


def idle_heartbeat(msg: str = "aguardando fila / próximo ciclo…") -> None:
    thr = SESSION.throughput_h()
    thr_s = f"  ·  {thr:.1f}/h" if thr else ""
    _emit(
        _c(
            C.DIM,
            f"  · [{_now()}] {msg}  ·  sessão {SESSION.elapsed()}  ·  "
            f"{SESSION.processadas} ok / {SESSION.com_inaja} Inajá / {SESSION.falhas} falhas"
            f"  ·  pts {SESSION.score()}{thr_s}",
        )
    )
    if SESSION.historico:
        _emit("    trilha  " + SESSION.spark_colored() + _c(C.DIM, "  (█ Inajá ▒ ok ░ falha)"))


class RichConsoleFormatter(logging.Formatter):
    """Formatter colorido para o console (arquivo usa Formatter plain)."""

    LEVEL_STYLES = {
        logging.DEBUG: (C.DIM, "DBG"),
        logging.INFO: (C.BRIGHT_CYAN, "INF"),
        logging.WARNING: (C.BRIGHT_YELLOW, "WRN"),
        logging.ERROR: (C.BRIGHT_RED, "ERR"),
        logging.CRITICAL: (C.BG_RED + C.BOLD + C.WHITE, "CRT"),
    }

    MOD_ICON = {
        "pipeline": "⚙",
        "scraper": "🌐",
        "downloader": "⬇",
        "ocr.extractor": "👁",
        "ocr.tesseract": "🔤",
        "ocr.cache": "💾",
        "detector": "🔍",
        "ai_processor": "✦",
        "notifier": "📣",
        "database": "🗄",
        "__main__": "▶",
        "main": "▶",
    }

    _SKIP_CONSOLE_SUBSTR = (
        "coluna(s) detectada(s)",
        "coluna ",
        "recuperado (psm",
        "ObjectCache",
        "LEAK!",
        "ainda has count",
    )

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno <= logging.INFO:
            for s in self._SKIP_CONSOLE_SUBSTR:
                if s in msg:
                    return ""

        style, tag = self.LEVEL_STYLES.get(
            record.levelno, (C.WHITE, record.levelname[:3])
        )
        name = record.name
        short = name if name.startswith("ocr.") else name.split(".")[-1]
        icon = self.MOD_ICON.get(name) or self.MOD_ICON.get(short) or "·"
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        colored_msg = msg
        if _ENABLED:
            low = msg.lower()
            if "inajá" in low or "inaja" in low:
                colored_msg = _c(C.BRIGHT_GREEN + C.BOLD, msg)
            elif "quarentena" in low:
                colored_msg = _c(C.BRIGHT_YELLOW + C.BOLD, msg)
            elif "falha" in low or "erro" in low:
                if record.levelno >= logging.WARNING:
                    colored_msg = _c(C.BRIGHT_RED, msg)
            elif "cache" in low:
                colored_msg = _c(C.MAGENTA, msg)
            elif "refinou" in low or "ia " in low:
                colored_msg = _c(C.BRIGHT_MAGENTA, msg)

        prefix = (
            f"{_c(C.DIM, ts)} "
            f"{_c(style + C.BOLD, tag)} "
            f"{icon} {_c(C.DIM, short)}"
        )
        return f"{prefix}  {colored_msg}"


class RichStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if not msg or not str(msg).strip():
                return
            self.stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def attach_rich_console(root_level: int = logging.INFO) -> None:
    enable_windows_ansi()
    # Força cor mesmo sob pipe (iniciar_tudo)
    global _ENABLED
    if not _NO_COLOR:
        _ENABLED = True
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(
            h, logging.FileHandler
        ):
            if getattr(h, "stream", None) in (sys.stdout, sys.stderr):
                root.removeHandler(h)
    console = RichStreamHandler(sys.stdout)
    console.setLevel(root_level)
    console.setFormatter(RichConsoleFormatter())
    root.addHandler(console)


def parse_progress_payload(msg: str | dict) -> tuple[int | None, int | None, str]:
    import re

    if isinstance(msg, dict):
        cur = msg.get("current")
        tot = msg.get("total")
        label = {
            "ocr_fast": "OCR rápido",
            "ocr_structured": "OCR estruturado",
            "ocr_completo": "OCR completo",
            "ocr": "OCR",
            "download": "Download",
            "detect": "Detecção",
            "ia": "IA",
        }.get(str(msg.get("step") or ""), str(msg.get("step") or "OCR"))
        try:
            return (
                int(cur) if cur is not None else None,
                int(tot) if tot is not None else None,
                label,
            )
        except (TypeError, ValueError):
            return None, None, label

    text = str(msg)
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m:
        label = "OCR"
        low = text.lower()
        if "rápido" in low or "rapido" in low:
            label = "OCR rápido"
        elif "estrutur" in low:
            label = "OCR estruturado"
        elif "pdfplumber" in low:
            label = "Texto nativo"
        elif "candidat" in low:
            label = "Candidatas"
        return int(m.group(1)), int(m.group(2)), label
    return None, None, text[:40]
