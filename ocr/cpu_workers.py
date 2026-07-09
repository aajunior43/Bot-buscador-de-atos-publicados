"""Paralelismo de OCR adaptativo ao uso de CPU.

Objetivo: manter o processador na faixa ~85–90% durante o OCR,
aumentando workers quando há folga e reduzindo se passar do teto.

Não exige psutil — usa GetSystemTimes (Windows) ou /proc/stat (Linux).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Sequence, TypeVar

from config import SETTINGS

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

_lock = threading.Lock()
_last_workers: int | None = None
_last_cpu: float | None = None
_last_sample_mono: float = 0.0

# Amostras anteriores de tempos ociosos/totais (para delta)
_prev_idle: float | None = None
_prev_total: float | None = None


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
            # kernel inclui idle no Windows
            idle_t = float(_u64(idle))
            kernel_t = float(_u64(kernel))
            user_t = float(_u64(user))
            total = kernel_t + user_t
            return idle_t, total
        except Exception:
            return None

    # Linux / outros com /proc/stat
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            line = fh.readline()
        if not line.startswith("cpu "):
            return None
        parts = [float(x) for x in line.split()[1:]]
        # user nice system idle iowait irq softirq steal ...
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)
        total = sum(parts)
        return idle, total
    except Exception:
        return None


def medir_cpu_percent(intervalo: float = 0.35) -> float | None:
    """CPU total do sistema 0–100. None se não for possível medir."""
    global _prev_idle, _prev_total
    t1 = _cpu_times()
    if t1 is None:
        return None
    time.sleep(max(0.05, intervalo))
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


def _cores() -> int:
    return max(1, os.cpu_count() or 2)


def _max_workers_cfg() -> int:
    """Teto de workers: OCR_MAX_WORKERS ou todos os cores."""
    cfg = int(getattr(SETTINGS, "ocr_max_workers", 0) or 0)
    cores = _cores()
    if cfg <= 0:
        # Deixa 0 cores livres só se houver muitos; em 4 cores usa os 4
        return cores if cores <= 4 else max(1, cores - 1)
    return max(1, min(cfg, cores * 2))  # permite um pouco acima se o user forçou


def _min_workers_cfg() -> int:
    cfg = int(getattr(SETTINGS, "ocr_min_workers", 0) or 0)
    if cfg > 0:
        return max(1, cfg)
    return 1


def _target_cpu() -> float:
    """Alvo 0–1 (ex.: 0.88 = 88%)."""
    raw = getattr(SETTINGS, "ocr_cpu_target", 0.88)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.88
    if v > 1.5:  # usuário passou 88 em vez de 0.88
        v = v / 100.0
    return max(0.50, min(0.95, v))


def adaptive_enabled() -> bool:
    return bool(getattr(SETTINGS, "ocr_adaptive_cpu", True))


def escolher_workers(
    *,
    n_tarefas: int | None = None,
    forcar_amostra: bool = False,
    label: str = "OCR",
) -> int:
    """Escolhe quantos workers usar agora (1 … max)."""
    global _last_workers, _last_cpu, _last_sample_mono

    max_w = _max_workers_cfg()
    min_w = min(_min_workers_cfg(), max_w)
    if n_tarefas is not None:
        max_w = max(1, min(max_w, int(n_tarefas)))
        min_w = min(min_w, max_w)

    if not adaptive_enabled():
        w = max(min_w, min(max_w, int(SETTINGS.ocr_max_workers or max_w) or max_w))
        return max(1, w)

    agora = time.monotonic()
    # Reusa decisão recente (evita sleep a cada página)
    if (
        not forcar_amostra
        and _last_workers is not None
        and (agora - _last_sample_mono) < 2.5
    ):
        return max(min_w, min(max_w, _last_workers))

    cpu = medir_cpu_percent(0.30)
    alvo = _target_cpu() * 100.0  # 88
    teto = min(95.0, alvo + 5.0)  # ~93
    piso = max(50.0, alvo - 8.0)  # ~80

    base = _last_workers if _last_workers is not None else max(min_w, (max_w + 1) // 2)

    if cpu is None:
        # Sem medição: sobe agressivo até o teto de cores
        w = max_w
    else:
        if cpu < piso:
            # Muita folga → sobe (mais se estiver bem ocioso)
            if cpu < piso - 15:
                w = min(max_w, base + 2)
            else:
                w = min(max_w, base + 1)
            # Se ainda estamos com poucos workers e CPU baixa, salta para estimativa
            if base <= 2 and cpu < 55 and max_w >= 3:
                w = max(w, min(max_w, _cores()))
        elif cpu > teto:
            w = max(min_w, base - 1)
            if cpu > 96:
                w = max(min_w, base - 2)
        else:
            # Na faixa-alvo: mantém, ou sobe 1 se ainda abaixo do centro
            if cpu < alvo and base < max_w:
                w = base + 1
            else:
                w = base
        _last_cpu = cpu

    w = max(min_w, min(max_w, w))
    with _lock:
        mudou = _last_workers != w
        _last_workers = w
        _last_sample_mono = time.monotonic()

    if mudou or forcar_amostra:
        cpu_s = f"{cpu:.0f}%" if cpu is not None else "?"
        logger.info(
            "OCR workers=%s (cpu=%s alvo=%.0f%% máx=%s) [%s]",
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
                f"workers={w} · cpu={cpu_s} · alvo={alvo:.0f}% · máx={max_w}",
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
    """Executa ``fn`` em paralelo com reajuste de workers entre ondas.

    Args:
        items: tarefas
        fn: worker
        on_done: callback (concluidas, total, item, resultado)
        wave_factor: tamanho da onda ≈ workers * wave_factor
    """
    if not items:
        return []

    total = len(items)
    results: list[R | None] = [None] * total
    # lista de (índice original, item)
    fila = list(enumerate(items))
    done = 0

    workers = escolher_workers(n_tarefas=total, forcar_amostra=True, label=label)

    while fila:
        tamanho_onda = max(workers * max(1, wave_factor), workers)
        onda = fila[:tamanho_onda]
        fila = fila[tamanho_onda:]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            fut_map = {pool.submit(fn, item): (idx, item) for idx, item in onda}
            for fut in as_completed(fut_map):
                idx, item = fut_map[fut]
                try:
                    res = fut.result()
                except Exception:
                    logger.exception("%s: falha no worker item=%s", label, idx)
                    raise
                results[idx] = res
                done += 1
                if on_done:
                    try:
                        on_done(done, total, item, res)
                    except Exception:
                        pass

        if fila:
            # Reamostra CPU após a onda e ajusta para a próxima
            workers = escolher_workers(
                n_tarefas=len(fila),
                forcar_amostra=True,
                label=f"{label}-onda",
            )

    return [r for r in results if r is not None]  # type: ignore[misc]


def map_parallel_indexed(
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    label: str = "OCR",
    on_done: Callable[[int, int], None] | None = None,
) -> dict[int, R]:
    """Como map_parallel, mas devolve dict índice→resultado (preserva None slots)."""
    if not items:
        return {}
    total = len(items)
    out: dict[int, R] = {}
    fila = list(enumerate(items))
    done = 0
    workers = escolher_workers(n_tarefas=total, forcar_amostra=True, label=label)

    while fila:
        tamanho_onda = max(workers, 1)
        # Ondas um pouco maiores que workers para manter a fila cheia
        tamanho_onda = min(len(fila), max(workers * 2, workers))
        onda = fila[:tamanho_onda]
        fila = fila[tamanho_onda:]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            fut_map = {pool.submit(fn, item): idx for idx, item in onda}
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                out[idx] = fut.result()
                done += 1
                if on_done:
                    on_done(done, total)

        if fila:
            workers = escolher_workers(
                n_tarefas=len(fila),
                forcar_amostra=True,
                label=f"{label}-onda",
            )
    return out
