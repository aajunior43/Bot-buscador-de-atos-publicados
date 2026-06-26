# -*- coding: utf-8 -*-
"""
Testes automatizados para a webapp (webapp.py).
Valida rotas de edições detectadas e visualização de detalhes.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
import database
from webapp import app


def test_edicoes_detectadas_vazia(db):
    client = TestClient(app)
    response = client.get("/edicoes-detectadas")
    assert response.status_code == 200
    assert "Lista" in response.text
    assert "Nenhuma" in response.text
    assert "detectada ainda" in response.text


def test_edicoes_detectadas_com_dados(db):
    eid = database.insert_or_get_edicao(
        "https://example.com/edicao_teste.pdf",
        "Edicao Especial de Teste",
        "2026-06-26",
    )
    client = TestClient(app)
    response = client.get("/edicoes-detectadas")
    assert response.status_code == 200
    assert "Especial de Teste" in response.text
    assert "pendente" in response.text


def test_edicao_detalhes_404(db):
    client = TestClient(app)
    response = client.get("/edicoes/99999")
    assert response.status_code == 404


def test_edicao_detalhes_sucesso(db):
    eid = database.insert_or_get_edicao(
        "https://example.com/edicao_detail_test.pdf",
        "Edicao de Detalhe",
        "2026-06-26",
    )
    database.update_ocr(eid, "dummy_ocr.txt", tem_inaja=True)

    publicacoes = [
        {
            "pagina": 1,
            "orgao": "Gabinete do Prefeito",
            "categoria": "Contratos",
            "categoria_ia": "Licitacoes",
            "valor": "R$ 10.000,00",
            "resumo_ia": "Contratacao de servicos de limpeza",
            "tipo": "Contrato",
            "numero": "123/2026",
            "data_documento": "2026-06-25",
            "bloco": 1,
            "assunto": "Contrato de servicos gerais",
            "trecho": "Bruto...",
            "texto_corrigido": "Corrigido...",
            "ia_processado": 1,
        }
    ]
    database.insert_publicacoes(eid, publicacoes)

    mencoes = [
        {"pagina": 1, "trecho": "Termo Inaja encontrado", "termo": "Inaja"}
    ]
    database.insert_mencoes(eid, mencoes)

    client = TestClient(app)
    response = client.get(f"/edicoes/{eid}")
    assert response.status_code == 200
    assert "de Detalhe" in response.text
    assert "Gabinete do Prefeito" in response.text
    assert "limpeza" in response.text
    assert "Inaja" in response.text
    assert "Copiar" in response.text


def test_detectar_edicoes_agora(db, monkeypatch):
    from scraper import Edicao
    monkeypatch.setattr(
        "webapp.coletar_edicoes",
        lambda: [
            Edicao(
                "https://example.com/ed_nova.pdf",
                "Edicao Nova",
                "2026-06-26",
            )
        ],
    )
    client = TestClient(app)
    response = client.post("/edicoes-detectadas/detectar", follow_redirects=False)
    assert response.status_code in [302, 303]
    assert response.headers["location"] == "/edicoes-detectadas"

