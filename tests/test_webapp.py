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


def test_dashboard_vazio(db):
    """Banco vazio não pode quebrar o dashboard (home de Atos)."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Atos de Inajá" in response.text
    assert "Leitura" in response.text
    assert "Operação" in response.text


def test_operacao_hub(db):
    client = TestClient(app)
    response = client.get("/operacao")
    assert response.status_code == 200
    assert "Painel operacional" in response.text
    assert "Saúde do sistema" in response.text


def test_revisao_so_mencao(db):
    eid = database.insert_or_get_edicao(
        "https://example.com/so_mencao.pdf",
        "Edicao So Mencao",
        "2026-06-11",
    )
    database.update_ocr(eid, "dummy.txt", tem_inaja=True)
    database.insert_mencoes(
        eid, [{"pagina": 1, "trecho": "...Inajá...", "termo": "Inajá"}]
    )
    client = TestClient(app)
    r = client.get("/revisao/so-mencao")
    assert r.status_code == 200
    assert "Só menção" in r.text or "só menção" in r.text.lower()
    assert "So Mencao" in r.text or "Mencao" in r.text

    r2 = client.post(
        f"/revisao/so-mencao/{eid}",
        data={"status": "ignorada", "next": "/revisao/so-mencao"},
        follow_redirects=False,
    )
    assert r2.status_code in (302, 303)
    # After ignorada, default list (pendentes only) should not show it
    r3 = client.get("/revisao/so-mencao")
    assert r3.status_code == 200



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


def test_analisar_lote_com_limite(db, monkeypatch):
    # Evita processamento real de OCR em background durante o teste
    monkeypatch.setattr("webapp._analisar_edicoes_lote", lambda ids: None)
    for i in range(3):
        database.insert_or_get_edicao(
            f"https://example.com/lote_{i}.pdf",
            f"Edicao Lote {i}",
            "2026-06-26",
        )
    client = TestClient(app)

    # Sem o campo limite -> usa o default (5) e continua compatível
    r = client.post("/edicoes-detectadas/analisar-lote", follow_redirects=False)
    assert r.status_code in [302, 303]
    assert r.headers["location"] == "/edicoes-detectadas"

    # Com o campo limite enviado pelo <select> do formulário
    r = client.post(
        "/edicoes-detectadas/analisar-lote",
        data={"limite": "20"},
        follow_redirects=False,
    )
    assert r.status_code in [302, 303]
    assert r.headers["location"] == "/edicoes-detectadas"

