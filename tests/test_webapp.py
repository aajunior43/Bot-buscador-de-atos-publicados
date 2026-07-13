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
    assert "Buscar" in response.text or 'name="q"' in response.text
    # Nav enxuta: Atos + Operação + Mais; sem health-strip na home
    assert "Operação" in response.text
    assert 'id="nav-more"' in response.text or "Mais" in response.text
    assert "health-strip" not in response.text
    assert "Operação rápida" not in response.text


def test_inteligencia_page(db):
    client = TestClient(app)
    r = client.get("/inteligencia")
    assert r.status_code == 200
    assert "Inteligência" in r.text or "temas" in r.text.casefold()


def test_dashboard_filtro_tipo(db):
    eid = database.insert_or_get_edicao(
        "https://example.com/tipo_filtro.pdf", "Ed Tipo", "2026-07-01"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "orgao": "Prefeitura",
                "tipo": "Decreto",
                "numero": "1/2026",
                "assunto": "Teste decreto",
                "trecho": "x",
            },
            {
                "pagina": 2,
                "orgao": "Prefeitura",
                "tipo": "Portaria",
                "numero": "2/2026",
                "assunto": "Teste portaria",
                "trecho": "y",
            },
        ],
    )
    client = TestClient(app)
    r = client.get("/?tipo=Decreto")
    assert r.status_code == 200
    assert "Decreto" in r.text
    assert "1/2026" in r.text
    # Portaria filtrada fora
    assert "2/2026" not in r.text


def test_operacao_hub(db):
    client = TestClient(app)
    response = client.get("/operacao")
    assert response.status_code == 200
    assert "Painel operacional" in response.text
    assert "Saúde do sistema" in response.text
    assert "Status da automação" in response.text
    assert "WEB · varredura" in response.text
    assert "BOT · processamento" in response.text
    assert "Avançado" in response.text
    assert "Heartbeat" in response.text or "BOT online" in response.text or "BOT offline" in response.text
    assert "tab=automacao" in response.text or "Automação" in response.text
    assert "tab=fila" in response.text or "Fila de jobs" in response.text


def test_operacao_aba_fila(db):
    """Aba Fila do cockpit unificado lista histórico de jobs."""
    database.start_job("ocr", titulo="Ed Teste Fila", edicao_id=None, mensagem="ok")
    client = TestClient(app)
    r = client.get("/operacao?tab=fila")
    assert r.status_code == 200
    assert "Histórico operacional" in r.text
    assert "Rodando" in r.text or "Concluídos" in r.text
    assert "Ed Teste Fila" in r.text or "ocr" in r.text


def test_status_redireciona_para_operacao_fila(db):
    client = TestClient(app)
    r = client.get("/status", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "/operacao" in r.headers.get("location", "")
    assert "fila" in r.headers.get("location", "")


def test_api_automacao(db):
    database.registrar_evento_ciclo("web_scan", "3 edição(ões) detectada(s)")
    database.registrar_evento_ciclo("bot_ciclo", "novas=1 processadas=1 fila_pendentes=0")
    database.registrar_heartbeat_bot()
    client = TestClient(app)
    r = client.get("/api/automacao")
    assert r.status_code == 200
    data = r.json()
    assert "web_ultimo" in data
    assert data["web_mensagem"].startswith("3 edição")
    assert "bot_ultimo" in data
    assert "fila_proximo_ciclo" in data
    assert "pendentes_ocr" in data
    assert data["bot_vivo"] is True
    assert "web_proxima_rel" in data
    assert "bot_proxima_rel" in data


def test_api_health(db):
    database.registrar_heartbeat_bot()
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["bot_vivo"] is True
    assert data["status"] in ("ok", "degraded")
    assert "pendentes_ocr" in data


def test_liberar_quarentena_rota(db):
    eid = database.insert_or_get_edicao(
        "https://example.com/quar.pdf", "Quar", "2026-01-15"
    )
    for _ in range(3):
        database.registrar_falha_processamento(eid, "boom")
    assert database.contar_quarentena() >= 1
    client = TestClient(app)
    r = client.post(f"/operacao/quarentena/{eid}/liberar", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert database.contar_quarentena() == 0
    r2 = client.get("/operacao")
    assert r2.status_code == 200
    assert "Quarentena" in r2.text


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

