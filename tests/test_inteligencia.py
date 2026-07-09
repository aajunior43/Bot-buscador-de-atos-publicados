"""Testes da camada de inteligência."""
from __future__ import annotations

from inteligencia import (
    campo_ancorado_no_trecho,
    montar_resumo_diario_texto,
    rankear_publicacoes,
    score_texto_candidatura,
    score_titulo_edicao,
    validar_campos_ia,
)


def test_score_inaja_alto():
    sr = score_texto_candidatura(
        "PREFEITURA MUNICIPAL DE INAJÁ Decreto Nº 10/2026 R$ 1.000,00",
        titulo="Jornal 01-06-2026",
    )
    assert sr.score >= 55
    assert sr.prioridade == 0


def test_score_vizinho_baixo():
    sr = score_texto_candidatura(
        "Prefeitura Municipal de Paranacity Portaria 1/2020",
        titulo="edicao",
    )
    assert sr.score < 55


def test_score_titulo():
    sr = score_titulo_edicao("Sem nada especial")
    assert 0 <= sr.score <= 100


def test_anti_alucinacao_remove_numero_inventado():
    pub = {"trecho": "DECRETO da Prefeitura Municipal de Inajá sobre obras"}
    ia = {"numero": "999/2099", "orgao": "Prefeitura Municipal de Inajá", "tipo": "Decreto"}
    out = validar_campos_ia(pub, ia)
    assert out.get("numero") in (None, "")
    assert out.get("orgao")  # órgão está no trecho


def test_campo_ancorado_valor():
    assert campo_ancorado_no_trecho("R$ 1.500,00", "valor de R$ 1.500,00 empenhado")
    assert not campo_ancorado_no_trecho("R$ 999.999,99", "sem valor aqui")


def test_rankear_publicacoes():
    rows = [
        {
            "tipo": "Portaria",
            "orgao": "Prefeitura",
            "resumo_ia": "nomeacao de servidor",
            "trecho": "portaria",
            "id": 1,
        },
        {
            "tipo": "Decreto",
            "orgao": "Camara",
            "resumo_ia": "obra de asfalto",
            "trecho": "decreto obra",
            "id": 2,
        },
    ]
    ranked = rankear_publicacoes("obra asfalto", rows, limit=5)
    assert ranked
    assert ranked[0]["id"] == 2


def test_resumo_texto():
    t = montar_resumo_diario_texto(
        dia="2026-07-09",
        n_pubs=2,
        n_edicoes_inaja=1,
        tipos=[("Decreto", 2)],
        valores_txt="R$ 10,00",
        destaques=["Decreto 1 — teste"],
    )
    assert "2026-07-09" in t
    assert "Decreto" in t


def test_feedback_e_score_db(db):
    import database

    eid = database.insert_or_get_edicao(
        "https://ex.com/intel.pdf", "Ed Inaja", "2026-06-01"
    )
    database.atualizar_score_edicao(eid, 80, 0)
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "orgao": "Prefeitura",
                "tipo": "Decreto",
                "numero": "1/2026",
                "trecho": "x",
                "assunto": "teste",
            }
        ],
    )
    with database.connect() as conn:
        pid = conn.execute("SELECT id FROM publicacoes LIMIT 1").fetchone()[0]
    assert database.set_feedback_publicacao(pid, "correto")
    assert database.set_feedback_publicacao(pid, "errado")
    n = database.recalcular_scores_pendentes(limit=50)
    assert n >= 0
