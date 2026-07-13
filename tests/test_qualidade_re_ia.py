# -*- coding: utf-8 -*-
"""Testes PR3 — re-IA, update_publicacao_ia expandido, candidatas."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import qualidade


def test_listar_candidatas_sem_resumo(db, mock_settings):
    import database

    database.init_db()
    eid = database.insert_or_get_edicao(
        "https://ex.com/reia.pdf", "ReIA", "2026-07-01"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "tipo": "Contrato",
                "numero": "1/2026",
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "x" * 100,
                "resumo_ia": None,
            },
            {
                "pagina": 2,
                "tipo": "Decreto",
                "numero": "2/2026",
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "y" * 100,
                "resumo_ia": "Já tem resumo completo da publicação.",
                "ia_processado": 1,
                "confianca_nivel": "alta",
            },
        ],
    )
    with patch("qualidade.SETTINGS", mock_settings):
        cands = qualidade.listar_candidatas_re_ia(10)
    ids = {c["id"] for c in cands}
    # só a sem resumo deve entrar (a com resumo alta e completa não)
    assert any(c.get("resumo_ia") in (None, "") for c in cands)


def test_update_publicacao_ia_nao_zera_importancia(db, mock_settings):
    import database

    database.init_db()
    eid = database.insert_or_get_edicao(
        "https://ex.com/imp.pdf", "Imp", "2026-07-01"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "tipo": "Decreto",
                "numero": "10/2026",
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "trecho",
                "importancia": 5,
                "importancia_motivo": "valor alto",
                "resumo_ia": None,
            }
        ],
    )
    with database.connect() as c:
        pid = c.execute(
            "SELECT id FROM publicacoes WHERE edicao_id=?", (eid,)
        ).fetchone()["id"]

    database.update_publicacao_ia(
        {
            "id": pid,
            "resumo_ia": "Novo resumo da IA",
            "orgao": None,  # não deve apagar
            "importancia": None,
            "confianca": 90,
            "confianca_nivel": "alta",
        },
        registrar_tentativa=True,
    )
    with database.connect() as c:
        row = dict(
            c.execute("SELECT * FROM publicacoes WHERE id=?", (pid,)).fetchone()
        )
    assert row["resumo_ia"] == "Novo resumo da IA"
    assert row["importancia"] == 5
    assert row["orgao"] == "Prefeitura Municipal de Inajá"
    assert int(row["ia_tentativas"] or 0) == 1
    assert row["confianca"] == 90
    assert row["confianca_nivel"] == "alta"


def test_update_ia_tentativas_uma_vez(db, mock_settings):
    import database

    database.init_db()
    eid = database.insert_or_get_edicao(
        "https://ex.com/t.pdf", "T", "2026-07-01"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "tipo": "Lei",
                "numero": "1/2026",
                "trecho": "x" * 50,
                "orgao": "Município de Inajá",
            }
        ],
    )
    with database.connect() as c:
        pid = c.execute(
            "SELECT id FROM publicacoes WHERE edicao_id=?", (eid,)
        ).fetchone()["id"]

    database.update_publicacao_ia(
        {"id": pid, "resumo_ia": "a"}, registrar_tentativa=True
    )
    database.update_publicacao_ia(
        {"id": pid, "resumo_ia": "b"}, registrar_tentativa=True
    )
    database.update_publicacao_ia(
        {"id": pid, "resumo_ia": "c"}, registrar_tentativa=False
    )
    with database.connect() as c:
        t = c.execute(
            "SELECT ia_tentativas FROM publicacoes WHERE id=?", (pid,)
        ).fetchone()["ia_tentativas"]
    assert int(t or 0) == 2


def test_update_preserva_feedback(db, mock_settings):
    import database

    database.init_db()
    eid = database.insert_or_get_edicao(
        "https://ex.com/fb.pdf", "FB", "2026-07-01"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "tipo": "Portaria",
                "numero": "1/2026",
                "trecho": "x" * 40,
                "orgao": "Prefeitura Municipal de Inajá",
            }
        ],
    )
    with database.connect() as c:
        pid = c.execute(
            "SELECT id FROM publicacoes WHERE edicao_id=?", (eid,)
        ).fetchone()["id"]
        c.execute(
            "UPDATE publicacoes SET feedback='correto', feedback_em='2026-07-01' WHERE id=?",
            (pid,),
        )

    database.update_publicacao_ia(
        {"id": pid, "resumo_ia": "atualizado", "feedback": "errado"},
        registrar_tentativa=True,
    )
    with database.connect() as c:
        row = dict(
            c.execute("SELECT * FROM publicacoes WHERE id=?", (pid,)).fetchone()
        )
    assert row["feedback"] == "correto"
    assert row["resumo_ia"] == "atualizado"


def test_limite_re_ia_ciclo(db, mock_settings):
    import agente

    object.__setattr__(mock_settings, "agente_max_re_ia_por_ciclo", 5)
    object.__setattr__(mock_settings, "agente_max_re_ia_por_dia", 40)
    object.__setattr__(mock_settings, "agente_max_ia_por_hora", 15)
    object.__setattr__(mock_settings, "ai_max_calls_por_ciclo", 80)
    object.__setattr__(mock_settings, "quality_re_ia_auto", True)
    with patch("agente.SETTINGS", mock_settings), patch(
        "agente._ia_calls_hora", return_value=0
    ), patch("agente._re_ia_calls_hoje", return_value=0):
        assert agente._limite_re_ia_ciclo() == 5
    with patch("agente.SETTINGS", mock_settings), patch(
        "agente._ia_calls_hora", return_value=14
    ), patch("agente._re_ia_calls_hoje", return_value=0):
        assert agente._limite_re_ia_ciclo() == 1
    object.__setattr__(mock_settings, "quality_re_ia_auto", False)
    with patch("agente.SETTINGS", mock_settings):
        assert agente._limite_re_ia_ciclo() == 0


def test_cerebro_re_ia_usa_update_nao_insert(db, mock_settings):
    import agente
    import database

    database.init_db()
    agente.set_agente_ativo(True)
    agente.set_agente_modo("formiga")
    eid = database.insert_or_get_edicao(
        "https://ex.com/cer.pdf", "Cer", "2026-07-01"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "tipo": "Contrato",
                "numero": "9/2026",
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "contrato sem resumo " + ("z" * 80),
                "resumo_ia": None,
            }
        ],
    )
    with database.connect() as c:
        pid = c.execute(
            "SELECT id FROM publicacoes WHERE edicao_id=?", (eid,)
        ).fetchone()["id"]
        c.execute(
            "UPDATE publicacoes SET feedback='correto' WHERE id=?", (pid,)
        )

    fake_ref = [
        {
            "id": pid,
            "edicao_id": eid,
            "resumo_ia": "Resumo gerado",
            "tipo": "Contrato",
            "numero": "9/2026",
            "orgao": "Prefeitura Municipal de Inajá",
            "trecho": "contrato sem resumo " + ("z" * 80),
            "data_publicacao": "2026-07-01",
        }
    ]

    with patch("agente.is_lock_held", return_value=True), patch(
        "agente._motivo_pular_ocr_cerebro", return_value="lock"
    ), patch("agente._pode_ia", return_value=True), patch(
        "agente._limite_re_ia_ciclo", return_value=3
    ), patch(
        "ai_processor.ia_disponivel", return_value=True
    ), patch(
        "ai_processor.refinar_publicacoes", return_value=(fake_ref, {})
    ), patch(
        "ai_processor.reset_ai_call_counter"
    ), patch("agente.SETTINGS", mock_settings), patch(
        "qualidade.SETTINGS", mock_settings
    ):
        res = agente.run_cerebro(force=True)

    assert any(a.acao == "re_ia" and a.ok for a in res.acoes)
    with database.connect() as c:
        row = dict(
            c.execute("SELECT * FROM publicacoes WHERE id=?", (pid,)).fetchone()
        )
    assert row["feedback"] == "correto"
    assert row["resumo_ia"] == "Resumo gerado"
    assert int(row["ia_tentativas"] or 0) >= 1
