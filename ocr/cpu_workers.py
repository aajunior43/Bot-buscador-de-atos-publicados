"""Paralelismo de OCR com CPU estável (sem serrar 100%↔50%).

Estratégia:
- Escolhe workers **uma vez** no início de cada fase (rápido / estruturado)
- Mantém **um único** ThreadPool até o fim (sem ondas que desligam a CPU)
- Histerese larga + média móvel se reamostrar
- ±1 worker no máximo, com cooldown longo

Não exige psutil — GetSystemTimes (Windows) ou /proc/stat (Linux).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Sequence, TypeVar

from config import SETTINGS

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

_lock = threading.Lock()
_last_workers: int | None = None
_last_cpu: float | None = None
_last_sample_mono: float = 0.0
_cpu_ema: float | None = None
_high_streak: int = 0
_low_streak: int = 0


def _cpu_times() -> tuple[float, float] | None:
    """Retorna (idle, total) em unidades arbitrárias, ou None."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = [
                    ("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD),
                ]

            def _u64(ft: FILETIME) -> int:
                return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

            idle = FILETIME()
            kernel = FILETIME()
            user = FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            idle_t = float(_u64(idle))
            kernel_t = float(_u64(kernel))
            user_t = float(_u64(user))
            total = kernel_t + user_t
            return idle_t, total
        except Exception:
            return None

    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            line = fh.readline()
        if not line.startswith("cpu "):
            return None
        parts = [float(x) for x in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)
        total = sum(parts)
        return idle, total
    except Exception:
        return None


def medir_cpu_percent(intervalo: float = 0.4) -> float | None:
    """CPU total do sistema 0–100. None se não for possível medir."""
    t1 = _cpu_times()
    if t1 is None:
        return None
    time.sleep(max(0.08, intervalo))
    t2 = _cpu_times()
    if t2 is None:
        return None
    idle1, total1 = t1
    idle2, total2 = t2
    didle = idle2 - idle1
    dtotal = total2 - total1
    if dtotal <= 0:
        return None
    busy = 1.0 - (didle / dtotal)
    return max(0.0, min(100.0, busy * 100.0))


def medir_cpu_media(amostras: int = 3, intervalo: float = 0.25) -> float | None:
    """Média de várias amostras (mais estável que um único snapshot)."""
    vals: list[float] = []
    for _ in range(max(1, amostras)):
        v = medir_cpu_percent(intervalo)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return sum(vals) / len(vals)


def _cores() -> int:
    return max(1, os.cpu_count() or 2)


def _max_workers_cfg() -> int:
    """Teto de workers: OCR_MAX_WORKERS ou cores-1 (nunca satura 100% por padrão).

    Sempre reserva pelo menos 1 núcleo para SO/UI, salvo se o usuário
    forçar OCR_MAX_WORKERS explicitamente.
    """
    cfg = int(getattr(SETTINGS, "ocr_max_workers", 0) or 0)
    cores = _cores()
    # Teto "seguro": nunca todos os cores no automático
    teto_seguro = max(1, cores - 1)
    if cfg <= 0:
        return teto_seguro
    # Usuário definiu teto manual — respeita, mas avisa no log se = todos os cores
    return max(1, min(cfg, cores))


def _min_workers_cfg() -> int:
    cfg = int(getattr(SETTINGS, "ocr_min_workers", 0) or 0)
    if cfg > 0:
        return max(1, cfg)
    return 1


def _target_cpu() -> float:
    """Alvo 0–1. Padrão ~70% para não ir a 100%."""
    raw = getattr(SETTINGS, "ocr_cpu_target", 0.70)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.70
    if v > 1.5:
        v = v / 100.0
    # Cap duro: nunca mira acima de 80% (evita saturar a máquina)
    return max(0.40, min(0.80, v))


def adaptive_enabled() -> bool:
    return bool(getattr(SETTINGS, "ocr_adaptive_cpu", True))


def _estimativa_por_ociosidade(cpu: float, max_w: int, min_w: int) -> int:
    """Escolha **conservadora** a partir da CPU antes do OCR.

    Cada worker Tesseract (OMP=1) ≈ 1 núcleo. Preferimos ficar abaixo do alvo
    a estourar 100%.
    """
    cores = _cores()
    alvo = _target_cpu()
    ocupado = max(0.0, min(1.0, cpu / 100.0))
    # Núcleos livres até o alvo (arredonda para baixo = mais seguro)
    livres_ate_alvo = max(0.0, cores * alvo - cores * ocupado)
    w = int(livres_ate_alvo)  # floor, não round
    if w < 1 and livres_ate_alvo >= 0.35:
        w = 1
    # Nunca empurra para o teto só porque a máquina está ociosa
    # (era isso que levava a 100% com 4 workers em 4 cores)
    w = max(min_w, min(max_w, w if w > 0 else min_w))
    return w


def escolher_workers(
    *,
    n_tarefas: int | None = None,
    forcar_amostra: bool = False,
    label: str = "OCR",
    modo: str = "inicio",
) -> int:
    """Escolhe quantos workers usar (1 … max).

    modo:
      - ``inicio``: medição com máquina ainda sem o pool OCR (preferido)
      - ``ajuste``: reamostra com histerese (raro; evita serrar)
    """
    global _last_workers, _last_cpu, _last_sample_mono, _cpu_ema
    global _high_streak, _low_streak

    max_w = _max_workers_cfg()
    min_w = min(_min_workers_cfg(), max_w)
    if n_tarefas is not None:
        max_w = max(1, min(max_w, int(n_tarefas)))
        min_w = min(min_w, max_w)

    if not adaptive_enabled():
        cfg = int(SETTINGS.ocr_max_workers or 0)
        w = max_w if cfg <= 0 else max(min_w, min(max_w, cfg))
        return max(1, w)

    agora = time.monotonic()
    # Cooldown longo: não fica redecidindo a cada onda
    cooldown = 12.0 if modo == "ajuste" else 1.0
    if (
        not forcar_amostra
        and _last_workers is not None
        and (agora - _last_sample_mono) < cooldown
    ):
        return max(min_w, min(max_w, _last_workers))

    if modo == "inicio":
        cpu = medir_cpu_media(amostras=2, intervalo=0.28)
    else:
        cpu = medir_cpu_percent(0.45)

    if cpu is not None:
        if _cpu_ema is None:
            _cpu_ema = cpu
        else:
            _cpu_ema = 0.55 * cpu + 0.45 * _cpu_ema
        cpu_ref = _cpu_ema
        _last_cpu = cpu
    else:
        cpu_ref = None

    alvo = _target_cpu() * 100.0
    # Faixa morta: só reage se saturar (>85%) ou ficar muito ocioso
    piso = max(35.0, alvo - 25.0)
    teto = min(85.0, alvo + 12.0)  # acima de 85% já reduz

    base = _last_workers

    if modo == "inicio" or base is None:
        if cpu_ref is None:
            # Sem medição: conservador (metade do teto seguro, min 1)
            w = max(min_w, min(max_w, (max_w + 1) // 2))
        else:
            w = _estimativa_por_ociosidade(cpu_ref, max_w, min_w)
        _high_streak = 0
        _low_streak = 0
    else:
        # Ajuste fino: ±1 no máximo; prioriza REDUZIR se CPU alta
        w = base
        if cpu_ref is None:
            pass
        elif cpu_ref > teto:
            _high_streak += 1
            _low_streak = 0
            if _high_streak >= 1:  # reage rápido a saturação
                w = max(min_w, base - 1)
                _high_streak = 0
        elif cpu_ref < piso:
            _low_streak += 1
            _high_streak = 0
            if _low_streak >= 3:  # sobe com muito mais cuidado
                w = min(max_w, base + 1)
                _low_streak = 0
        else:
            _high_streak = 0
            _low_streak = 0
            w = base

    w = max(min_w, min(max_w, w))
    with _lock:
        mudou = _last_workers != w
        _last_workers = w
        _last_sample_mono = time.monotonic()

    if mudou or (forcar_amostra and modo == "inicio"):
        cpu_s = f"{cpu_ref:.0f}%" if cpu_ref is not None else "?"
        logger.info(
            "OCR workers=%s (cpu=%s alvo=%.0f%% teto_seguro=%s) [%s]",
            w,
            cpu_s,
            alvo,
            max_w,
            label,
        )
        try:
            import console_ui

            console_ui.step(
                "CPU/OCR",
                f"workers={w}/{max_w} · cpu={cpu_s} · alvo≤{alvo:.0f}% (sem saturar)",
                ok=True,
            )
        except Exception:
            pass
    return w


def map_parallel(
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    label: str = "OCR",
    on_done: Callable[[int, int, T, R], None] | None = None,
    wave_factor: int = 1,
) -> list[R]:
    """Executa ``fn`` em paralelo com pool único (sem serrar CPU)."""
    if not items:
        return []

    total = len(items)
    results: list[R | None] = [None] * total
    workers = escolher_workers(
        n_tarefas=total, forcar_amostra=True, label=label, modo="inicio"
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_map = {
            pool.submit(fn, item): (idx, item) for idx, item in enumerate(items)
        }
        done = 0
        for fut in as_completed(fut_map):
            idx, item = fut_map[fut]
            res = fut.result()
            results[idx] = res
            done += 1
            if on_done:
                try:
                    on_done(done, total, item, res)
                except Exception:
                    pass

    return [r for r in results if r is not None]  # type: ignore[misc]


def map_parallel_indexed(
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    label: str = "OCR",
    on_done: Callable[[int, int], None] | None = None,
) -> dict[int, R]:
    """Pool único do início ao fim — CPU estável em platô alto."""
    if not items:
        return {}
    total = len(items)
    out: dict[int, R] = {}
    workers = escolher_workers(
        n_tarefas=total, forcar_amostra=True, label=label, modo="inicio"
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_map = {pool.submit(fn, item): idx for idx, item in enumerate(items)}
        done = 0
        for fut in as_completed(fut_map):
            idx = fut_map[fut]
            out[idx] = fut.result()
            done += 1
            if on_done:
                on_done(done, total)

    return out
