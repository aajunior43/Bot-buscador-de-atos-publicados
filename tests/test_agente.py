# -*- coding: utf-8 -*-
"""Testes do agente de vigilância (sem OCR real)."""
from __future__ import annotations


def test_modo_efetivo_e_set(db):
    from agente import modo_efetivo, set_agente_modo, set_agente_ativo, agente_esta_ativo

    set_agente_ativo(True)
    assert agente_esta_ativo() is True
    set_agente_modo("escudo")
    assert modo_efetivo() == "escudo"
    set_agente_modo("auto")
    assert modo_efetivo() == "auto"
    set_agente_ativo(False)
    assert agente_esta_ativo() is False
    set_agente_ativo(True)


def test_run_pulse_escudo(db):
    from agente import set_agente_modo, set_agente_ativo, run_pulse

    set_agente_ativo(True)
    set_agente_modo("escudo")
    res = run_pulse(force=True)
    assert res.ciclo == "pulse"
    assert res.modo == "escudo"
    assert any(a.acao == "estado" for a in res.acoes)


def test_run_cerebro_sentinela(db):
    from agente import set_agente_modo, set_agente_ativo, run_cerebro

    set_agente_ativo(True)
    set_agente_modo("sentinela")
    res = run_cerebro(force=True)
    assert res.ciclo == "cerebro"
    assert any(a.acao == "skip" for a in res.acoes)


def test_status_e_log(db):
    from agente import (
        set_agente_ativo,
        set_agente_modo,
        run_pulse,
        status_agente,
        listar_log,
        log_acao,
    )

    set_agente_ativo(True)
    set_agente_modo("escudo")
    run_pulse(force=True)
    log_acao(ciclo="pulse", modo="escudo", acao="teste", detalhe="unit")
    st = status_agente()
    assert st["ativo"] is True
    assert "modo_config" in st
    rows = listar_log(5)
    assert isinstance(rows, list)
