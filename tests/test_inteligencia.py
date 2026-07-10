"""Testes da camada de inteligência."""
from __future__ import annotations

from inteligencia import (
    campo_ancorado_no_trecho,
    detectar_anomalia,
    eh_radar_lrf,
    montar_checklist_local,
    montar_resumo_diario_texto,
    normalizar_lista_temas,
    normalizar_tema,
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


def test_normalizar_temas():
    assert normalizar_tema("Licitação") == "licitacao"
    assert normalizar_tema("RGF") == "fiscal"
    assert "obra" in normalizar_lista_temas(["obras", "asfalto", "xyz"])


def test_checklist_local():
    chk = montar_checklist_local(
        {
            "numero": "1/2026",
            "orgao": "Prefeitura",
            "tipo": "Portaria",
            "assunto": "nomeacao",
            "trecho": "PORTARIA com fundamentação legal",
        }
    )
    assert chk["tem_numero"] is True
    assert chk["tem_orgao"] is True
    assert chk["score"] >= 50


def test_anomalia_valor_alto_dispensa(mock_settings):
    is_a, motivo = detectar_anomalia(
        {"tipo": "Dispensa", "valor": "R$ 250.000,00", "importancia": 4}
    )
    assert is_a is True
    assert motivo


def test_anomalia_mediana():
    hist = [1000.0, 1200.0, 1100.0, 900.0, 1300.0, 1000.0]
    is_a, motivo = detectar_anomalia(
        {"tipo": "Contrato", "valor": "R$ 50.000,00"}, historico_valores=hist
    )
    assert is_a is True
    assert "mediana" in motivo.casefold() or "×" in motivo or "x" in motivo.casefold()


def test_radar_lrf():
    assert eh_radar_lrf({"tipo": "RGF", "resumo_ia": "gestão fiscal"})
    assert not eh_radar_lrf({"tipo": "Portaria", "assunto": "ferias"})


def test_validacao_flags():
    pub = {"trecho": "DECRETO da Prefeitura Municipal de Inajá sobre obras"}
    ia = {"numero": "999/2099", "orgao": "Prefeitura Municipal de Inajá"}
    out = validar_campos_ia(pub, ia)
    assert out.get("_validacao")
    assert out["_validacao"]["ok"] is False
    assert "numero" in out["_validacao"]["flags"][0]["campo"]


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
