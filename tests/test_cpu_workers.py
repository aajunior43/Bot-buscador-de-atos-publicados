"""Testes do seletor adaptativo de workers OCR."""
from __future__ import annotations

from ocr import cpu_workers


def test_escolher_workers_sem_adaptativo(mock_settings, monkeypatch):
    object.__setattr__(mock_settings, "ocr_adaptive_cpu", False)
    object.__setattr__(mock_settings, "ocr_max_workers", 2)
    object.__setattr__(mock_settings, "ocr_min_workers", 1)
    monkeypatch.setattr(cpu_workers, "SETTINGS", mock_settings)
    w = cpu_workers.escolher_workers(n_tarefas=10, forcar_amostra=True, modo="inicio")
    assert w == 2


def test_escolher_workers_respeita_n_tarefas(mock_settings, monkeypatch):
    object.__setattr__(mock_settings, "ocr_adaptive_cpu", False)
    object.__setattr__(mock_settings, "ocr_max_workers", 8)
    object.__setattr__(mock_settings, "ocr_min_workers", 1)
    monkeypatch.setattr(cpu_workers, "SETTINGS", mock_settings)
    w = cpu_workers.escolher_workers(n_tarefas=1, forcar_amostra=True, modo="inicio")
    assert w == 1


def test_map_parallel_indexed_pool_unico(mock_settings, monkeypatch):
    object.__setattr__(mock_settings, "ocr_adaptive_cpu", False)
    object.__setattr__(mock_settings, "ocr_max_workers", 2)
    monkeypatch.setattr(cpu_workers, "SETTINGS", mock_settings)
    monkeypatch.setattr(cpu_workers, "medir_cpu_percent", lambda interval=0.3: 50.0)
    monkeypatch.setattr(cpu_workers, "medir_cpu_media", lambda **kw: 50.0)

    items = [1, 2, 3, 4]
    out = cpu_workers.map_parallel_indexed(
        items, lambda x: x * 10, label="test"
    )
    assert sorted(out.values()) == [10, 20, 30, 40]


def test_estimativa_ociosa_usa_quase_todos_cores(mock_settings, monkeypatch):
    object.__setattr__(mock_settings, "ocr_adaptive_cpu", True)
    object.__setattr__(mock_settings, "ocr_max_workers", 0)
    object.__setattr__(mock_settings, "ocr_min_workers", 1)
    object.__setattr__(mock_settings, "ocr_cpu_target", 0.88)
    monkeypatch.setattr(cpu_workers, "SETTINGS", mock_settings)
    monkeypatch.setattr(cpu_workers, "_cores", lambda: 4)
    monkeypatch.setattr(cpu_workers, "medir_cpu_media", lambda **kw: 25.0)
    cpu_workers._last_workers = None
    w = cpu_workers.escolher_workers(n_tarefas=20, forcar_amostra=True, modo="inicio")
    # Com 4 cores e CPU 25%, deve ir para o teto (4)
    assert w == 4


def test_target_cpu_aceita_porcentagem(mock_settings, monkeypatch):
    object.__setattr__(mock_settings, "ocr_cpu_target", 88)
    monkeypatch.setattr(cpu_workers, "SETTINGS", mock_settings)
    assert abs(cpu_workers._target_cpu() - 0.88) < 0.001
