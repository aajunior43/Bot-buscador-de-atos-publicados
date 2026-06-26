"""
Testes unitários para notifier.py
Cobre montagem de mensagem Telegram e detecção de publicações oficiais.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest
from detector import DetectionResult
from scraper import Edicao


def _make_resultado(publicacoes=None, trechos=None, encontrado=True):
    return DetectionResult(
        encontrado=encontrado,
        edicao_id=42,
        edicao_titulo="Edição 25/06/2026",
        paginas_com_mencao=[1, 3],
        trechos=trechos or [{"pagina": 1, "trecho": "...Inajá..."}],
        termos_encontrados=["Inajá"],
        mencoes_db=[],
        publicacoes=publicacoes or [],
    )


def _make_edicao():
    return Edicao(
        url="https://example.com/edicao25062026.pdf",
        titulo="Edição 25/06/2026",
        data_publicacao="2026-06-25",
    )


class TestMontarMensagem:
    def test_mensagem_com_publicacao_oficial(self):
        from notifier import montar_mensagem
        pubs = [{"orgao": "Prefeitura Municipal de Inajá", "tipo": "Decreto", "numero": "001/2026", "pagina": 1, "categoria": "publicacao_oficial"}]
        resultado = _make_resultado(publicacoes=pubs)
        msg = montar_mensagem(resultado, _make_edicao())
        assert "Publicação oficial" in msg
        assert "Prefeitura" in msg

    def test_mensagem_sem_publicacao_oficial(self):
        from notifier import montar_mensagem
        resultado = _make_resultado(publicacoes=[])
        msg = montar_mensagem(resultado, _make_edicao())
        assert "Menção a Inajá detectada" in msg

    def test_mensagem_contem_url(self):
        from notifier import montar_mensagem
        resultado = _make_resultado()
        msg = montar_mensagem(resultado, _make_edicao())
        assert "https://example.com" in msg

    def test_mensagem_paginas(self):
        from notifier import montar_mensagem
        resultado = _make_resultado()
        msg = montar_mensagem(resultado, _make_edicao())
        assert "1" in msg and "3" in msg

    def test_mensagem_multiplas_publicacoes_truncada(self):
        from notifier import montar_mensagem
        pubs = [
            {"orgao": f"Órgão {i}", "tipo": "Decreto", "numero": f"00{i}/2026", "pagina": i, "categoria": "publicacao_oficial"}
            for i in range(8)
        ]
        resultado = _make_resultado(publicacoes=pubs)
        msg = montar_mensagem(resultado, _make_edicao())
        assert "omitida" in msg  # limita em 5 e mostra "omitidas"

    def test_mensagem_trechos_truncados(self):
        from notifier import montar_mensagem
        trechos = [{"pagina": i, "trecho": f"...trecho {i}..."} for i in range(15)]
        resultado = _make_resultado(trechos=trechos)
        msg = montar_mensagem(resultado, _make_edicao())
        assert "omitido" in msg


class TestTemPublicacaoOficial:
    def test_detecta_publicacao_com_orgao(self):
        from notifier import _tem_publicacao_oficial
        resultado = _make_resultado(publicacoes=[{"orgao": "Prefeitura", "tipo": None, "categoria": "publicacao_oficial"}])
        assert _tem_publicacao_oficial(resultado) is True

    def test_detecta_publicacao_com_tipo(self):
        from notifier import _tem_publicacao_oficial
        resultado = _make_resultado(publicacoes=[{"orgao": None, "tipo": "Decreto", "categoria": "publicacao_oficial"}])
        assert _tem_publicacao_oficial(resultado) is True

    def test_nao_detecta_sem_orgao_tipo(self):
        from notifier import _tem_publicacao_oficial
        resultado = _make_resultado(publicacoes=[{"orgao": None, "tipo": None, "categoria": "materia_jornalistica"}])
        assert _tem_publicacao_oficial(resultado) is False

    def test_nao_detecta_sem_publicacoes(self):
        from notifier import _tem_publicacao_oficial
        resultado = _make_resultado(publicacoes=[])
        assert _tem_publicacao_oficial(resultado) is False


class TestNotificar:
    def test_nao_notifica_se_nao_encontrado(self, db):
        from notifier import notificar
        import database
        database.init_db()
        resultado = _make_resultado(encontrado=False)
        # Não deve lançar exceção nem gravar notificação
        notificar(resultado, _make_edicao())
        notifs = database.get_notificacoes()
        assert len(notifs) == 0

    def test_notifica_salva_arquivo(self, db, tmp_path):
        from notifier import notificar
        import database
        database.init_db()
        resultado = _make_resultado(encontrado=True)
        # Sem Telegram configurado, deve salvar em arquivo
        notificar(resultado, _make_edicao())
        notifs = database.get_notificacoes()
        assert len(notifs) >= 1

    def test_envia_webhook(self, db):
        from notifier import notificar
        import database
        database.init_db()
        database.upsert_webhook("https://webhook.example.com/test", "Teste")
        resultado = _make_resultado(encontrado=True)
        with patch("notifier.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            notificar(resultado, _make_edicao())
            # O webhook é disparado em thread separada — aguardar brevemente
            import time
            time.sleep(0.2)
            # Verifica se foi chamado
            mock_post.assert_called()
