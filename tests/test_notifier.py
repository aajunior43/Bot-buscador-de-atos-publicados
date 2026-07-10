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


class TestTelegramStatusETeste:
    def test_status_telegram_sem_creds(self, db, mock_settings):
        from notifier import status_telegram

        with patch("notifier.SETTINGS", mock_settings):
            st = status_telegram()
        assert st["token_presente"] is False
        assert st["chat_id_presente"] is False
        assert st["pronto"] is False
        assert st["chat_id"] == ""

    def test_status_telegram_db_override(self, db, mock_settings):
        import database
        from notifier import status_telegram

        database.set_setting("telegram_bot_token", "123456:ABCDEFtoken")
        database.set_setting("telegram_chat_id", "-100999")
        with patch("notifier.SETTINGS", mock_settings):
            st = status_telegram()
        assert st["token_presente"] is True
        assert st["chat_id_presente"] is True
        assert st["pronto"] is True
        assert st["chat_id"] == "-100999"
        assert st["token_masked"].startswith("…") or st["token_masked"] == "***"

    def test_enviar_teste_retorna_dict_arquivo(self, db, mock_settings):
        from notifier import enviar_teste

        with patch("notifier.SETTINGS", mock_settings), \
             patch("notifier._disparar_webhooks"):
            info = enviar_teste()
        assert info["ok"] is True
        assert info["canal"] == "arquivo"
        assert "token_presente" in info


class TestEscapeMarkdownV2:
    def test_escapa_caracteres_especiais(self):
        from notifier import _escape_mdv2
        entrada = "Lei_1.000-A [teste] (x)!"
        saida = _escape_mdv2(entrada)
        # Cada caractere especial deve estar precedido de barra invertida
        for ch in ["_", ".", "-", "[", "]", "(", ")", "!"]:
            assert "\\" + ch in saida

    def test_mensagem_com_titulo_especial_escapada(self):
        from notifier import montar_mensagem
        resultado = _make_resultado()
        edicao = Edicao(
            url="https://example.com/ed.pdf",
            titulo="Edicao_25.06-2026 [oficial]",
            data_publicacao="2026-06-25",
        )
        msg = montar_mensagem(resultado, edicao)
        # Titulo deve aparecer escapado, sem sublinhado/ponto crus do titulo
        assert r"Edicao\_25\.06\-2026" in msg


class TestAnomaliaMensagem:
    def test_prefixo_anomalia_na_mensagem(self, db, mock_settings):
        import notifier

        object.__setattr__(mock_settings, "ai_importancia", False)
        object.__setattr__(mock_settings, "telegram_bot_token", "")
        object.__setattr__(mock_settings, "telegram_chat_id", "")
        pubs = [
            {
                "tipo": "Dispensa",
                "orgao": "Prefeitura",
                "pagina": 1,
                "categoria": "publicacao_oficial",
                "anomalia": 1,
                "anomalia_motivo": "valor 3x mediana",
                "valor": "R$ 200.000,00",
            }
        ]
        resultado = _make_resultado(publicacoes=pubs)
        with patch("notifier.SETTINGS", mock_settings), \
             patch("notifier._disparar_webhooks"), \
             patch("notifier._enviar_email", return_value=False):
            notifier.notificar(resultado, _make_edicao())
        # mensagem montada deve incluir anomalia — confere via arquivo salvo
        import database
        notifs = database.get_notificacoes()
        assert notifs
        conteudo = notifs[0]["conteudo"] or ""
        assert "ANOMALIA" in conteudo or "anomalia" in conteudo.casefold()


class TestFiltroImportancia:
    def test_suprimir_baixa_importancia(self, mock_settings):
        from notifier import _publicacoes_para_alerta

        object.__setattr__(mock_settings, "ai_importancia", True)
        object.__setattr__(mock_settings, "ai_importancia_min_notificar", 3)
        pubs = [
            {"tipo": "Aviso", "importancia": 1, "notificar_ia": False},
            {"tipo": "Decreto", "importancia": 4, "notificar_ia": True},
        ]
        resultado = _make_resultado(publicacoes=pubs)
        with patch("notifier.SETTINGS", mock_settings):
            filtradas = _publicacoes_para_alerta(resultado)
        assert len(filtradas) == 1
        assert filtradas[0]["tipo"] == "Decreto"

    def test_desligado_retorna_todas(self, mock_settings):
        from notifier import _publicacoes_para_alerta

        object.__setattr__(mock_settings, "ai_importancia", False)
        pubs = [{"importancia": 1}, {"importancia": 5}]
        resultado = _make_resultado(publicacoes=pubs)
        with patch("notifier.SETTINGS", mock_settings):
            assert len(_publicacoes_para_alerta(resultado)) == 2

    def test_sem_campo_usa_limiar(self, mock_settings):
        from notifier import _publicacoes_para_alerta

        object.__setattr__(mock_settings, "ai_importancia", True)
        object.__setattr__(mock_settings, "ai_importancia_min_notificar", 3)
        # sem importancia/notificar_ia → trata como limiar (entra)
        pubs = [{"tipo": "Portaria"}]
        resultado = _make_resultado(publicacoes=pubs)
        with patch("notifier.SETTINGS", mock_settings):
            assert len(_publicacoes_para_alerta(resultado)) == 1


class TestFallbackNotificacao:
    def test_fallback_para_email_quando_telegram_falha(self, db, mock_settings):
        import database
        import notifier
        database.init_db()
        object.__setattr__(mock_settings, "telegram_bot_token", "token")
        object.__setattr__(mock_settings, "telegram_chat_id", "123")
        resultado = _make_resultado(encontrado=True)
        with patch("notifier.SETTINGS", mock_settings), \
             patch("notifier._enviar_telegram_com_retry", side_effect=RuntimeError("falha tg")), \
             patch("notifier._enviar_email", return_value=True) as mock_email, \
             patch("notifier._disparar_webhooks"):
            notifier.notificar(resultado, _make_edicao())
        mock_email.assert_called_once()
        notifs = database.get_notificacoes()
        assert notifs[0]["canal"] == "email"

    def test_fallback_para_arquivo_quando_tudo_falha(self, db, mock_settings):
        import database
        import notifier
        database.init_db()
        object.__setattr__(mock_settings, "telegram_bot_token", "token")
        object.__setattr__(mock_settings, "telegram_chat_id", "123")
        resultado = _make_resultado(encontrado=True)
        with patch("notifier.SETTINGS", mock_settings), \
             patch("notifier._enviar_telegram_com_retry", side_effect=RuntimeError("falha tg")), \
             patch("notifier._enviar_email", return_value=False), \
             patch("notifier._disparar_webhooks"):
            notifier.notificar(resultado, _make_edicao())
        notifs = database.get_notificacoes()
        assert notifs[0]["canal"] == "arquivo"
