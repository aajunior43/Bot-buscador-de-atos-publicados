"""Testes do lock de processamento concorrente."""
from __future__ import annotations

import threading
from pathlib import Path

from process_lock import (
    ProcessLockError,
    is_lock_held,
    lock_holder_text,
    lock_status,
    process_lock,
)


def test_lock_exclusivo(tmp_path: Path):
    lock_file = tmp_path / "t.lock"
    acquired_second = []

    def other():
        try:
            with process_lock(lock_file, timeout=0.3, poll=0.05, label="other"):
                acquired_second.append(True)
        except ProcessLockError:
            acquired_second.append(False)

    with process_lock(lock_file, timeout=2.0, label="main"):
        t = threading.Thread(target=other)
        t.start()
        t.join(timeout=3)
        assert acquired_second == [False]

    # Após liberar, consegue adquirir
    with process_lock(lock_file, timeout=1.0, label="after"):
        pass


def test_is_lock_held_e_status(tmp_path: Path):
    """Probe non-blocking distingue arquivo residual vs lock OS real."""
    lock_file = tmp_path / "held.lock"
    assert is_lock_held(lock_file) is False
    assert lock_status(lock_file)["held"] is False

    # Outra thread detém o lock (mais fiel a multi-processo que same-handle)
    ready = threading.Event()
    release = threading.Event()

    def holder():
        with process_lock(lock_file, timeout=2.0, label="worker-a"):
            ready.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert ready.wait(timeout=3), "holder não adquiriu lock a tempo"
    try:
        assert is_lock_held(lock_file) is True
        st = lock_status(lock_file)
        assert st["held"] is True
        assert st["exists"] is True
        # Em alguns Windows a leitura do holder falha enquanto o byte-lock está
        # ativo; o importante é held=True.
        holder = st.get("holder") or lock_holder_text(lock_file) or ""
        if holder:
            assert "worker-a" in holder

    finally:
        release.set()
        t.join(timeout=3)

    # Arquivo residual permanece, mas OS lock liberado
    assert lock_file.exists()
    assert is_lock_held(lock_file) is False
    assert lock_status(lock_file)["held"] is False
