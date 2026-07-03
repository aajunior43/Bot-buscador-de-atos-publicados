"""Testes para scraper.py: normalizacao de data, parsing HTML e limite por ciclo."""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestNormalizarData:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("Edicao de 25/06/2026", "2026-06-25"),
            ("25-06-2026", "2026-06-25"),
            ("sem data aqui", None),
            ("data 32/13/2026 invalida", None),
        ],
    )
    def test_normalizar_data(self, texto, esperado):
        from scraper import _normalizar_data
        assert _normalizar_data(texto) == esperado


class TestExtrairComBs4:
    def test_filtra_e_ordena(self):
        from scraper import _extrair_com_bs4
        html = """
        <html><body>
          <a href="/edicoes/ed-10-06-2026.pdf">Edicao 10/06/2026</a>
          <a href="/edicoes/ed-25-06-2026.pdf">Edicao 25/06/2026</a>
          <a href="mailto:contato@example.com">Fale conosco</a>
          <a href="javascript:void(0)">Menu</a>
          <a href="/institucional">Sobre nos</a>
        </body></html>
        """
        edicoes = _extrair_com_bs4(html, "https://example.com/")
        # Apenas os 2 PDFs com data devem entrar; mailto/js/ancora sem data ficam de fora
        assert len(edicoes) == 2
        # Ordenacao decrescente por data
        assert edicoes[0].data_publicacao == "2026-06-25"
        assert edicoes[1].data_publicacao == "2026-06-10"

    def test_deduplica_por_url(self):
        from scraper import _extrair_com_bs4
        html = """
        <a href="/ed.pdf">Edicao 01/06/2026</a>
        <a href="/ed.pdf">Edicao 01/06/2026 (duplicada)</a>
        """
        edicoes = _extrair_com_bs4(html, "https://example.com/")
        assert len(edicoes) == 1


class TestListarEdicoes:
    def test_respeita_limite_e_ignora_existentes(self, mock_settings):
        from scraper import listar_edicoes, Edicao
        object.__setattr__(mock_settings, "max_edicoes_por_ciclo", 2)
        coletadas = [
            Edicao(url=f"https://example.com/ed{i}.pdf", titulo=f"ed{i}", data_publicacao=f"2026-06-{i:02d}")
            for i in range(1, 6)
        ]
        with patch("scraper.coletar_edicoes", return_value=coletadas), \
             patch("scraper.SETTINGS", mock_settings), \
             patch("scraper.database.url_exists", side_effect=lambda url: url.endswith("ed1.pdf")):
            resultado = listar_edicoes()
        # ed1 ja existe -> removida; restam 4 novas, limitadas a 2
        assert len(resultado) == 2
        assert all(not e.url.endswith("ed1.pdf") for e in resultado)
