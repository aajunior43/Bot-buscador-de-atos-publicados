"""Testes para exporter.py: exportacao CSV e JSON."""
from __future__ import annotations

import csv
import io

import pytest


@pytest.fixture
def exp(db, mock_settings, monkeypatch):
    """Garante que o exporter use o banco de teste."""
    import exporter
    monkeypatch.setattr(exporter, "SETTINGS", mock_settings)
    return exporter


def _popular(db):
    import database
    eid = database.insert_or_get_edicao(
        "https://example.com/ed.pdf", "Edicao 25/06/2026", "2026-06-25"
    )
    database.insert_publicacoes(
        eid,
        [
            {
                "pagina": 1,
                "categoria": "publicacao_oficial",
                "orgao": "Prefeitura Municipal de Inaja",
                "tipo": "Decreto",
                "numero": "001/2026",
                "assunto": "Nomeacao",
                "valor": "R$ 1.000,00",
            }
        ],
    )
    return eid


class TestExportarCsv:
    def test_csv_com_dados(self, exp, db):
        _popular(db)
        conteudo = exp.exportar_csv()
        linhas = list(csv.reader(io.StringIO(conteudo), delimiter=";"))
        assert linhas[0][:4] == ["id", "edicao_id", "edicao_titulo", "data_publicacao"]
        assert len(linhas) == 2  # cabecalho + 1 registro
        assert "Decreto" in conteudo
        assert "Prefeitura Municipal de Inaja" in conteudo

    def test_csv_vazio_so_cabecalho(self, exp, db):
        conteudo = exp.exportar_csv()
        linhas = [l for l in conteudo.splitlines() if l.strip()]
        assert len(linhas) == 1  # apenas cabecalho


class TestExportarJson:
    def test_json_com_dados(self, exp, db):
        _popular(db)
        dados = exp.exportar_json()
        assert isinstance(dados, list)
        assert len(dados) == 1
        assert dados[0]["tipo"] == "Decreto"
        assert dados[0]["orgao"] == "Prefeitura Municipal de Inaja"

    def test_json_vazio(self, exp, db):
        assert exp.exportar_json() == []

    def test_filtro_por_tipo(self, exp, db):
        _popular(db)
        assert len(exp.exportar_json(tipo="Decreto")) == 1
        assert len(exp.exportar_json(tipo="Portaria")) == 0
