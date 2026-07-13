"""
Testes unitários para notifier.py
Cobre montagem de mensagem e gravação em arquivo.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
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
        assert "omitida" in msg

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
        notificar(resultado, _make_edicao())
        notifs = database.get_notificacoes()
        assert len(notifs) == 0

    def test_notifica_salva_arquivo(self, db, tmp_path):
        from notifier import notificar
        import database
        database.init_db()
        resultado = _make_resultado(encontrado=True)
        notificar(resultado, _make_edicao())
        notifs = database.get_notificacoes()
        assert len(notifs) >= 1
        assert notifs[0]["canal"] == "arquivo"

    def test_envia_webhook(self, db):
        from notifier import notificar
        import database
        database.init_db()
        database.upsert_webhook("https://webhook.example.com/test", "Teste")
        resultado = _make_resultado(encontrado=True)
        with patch("notifier.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
            notificar(resultado, _make_edicao())
            import time
            time.sleep(0.2)
            mock_post.assert_called()


class TestEnviarTeste:
    def test_enviar_teste_retorna_dict_arquivo(self, db, mock_settings):
        from notifier import enviar_teste

        with patch("notifier.SETTINGS", mock_settings), \
             patch("notifier._disparar_webhooks"):
            info = enviar_teste()
        assert info["ok"] is True
        assert info["canal"] == "arquivo"
        assert "detalhe" in info


class TestAnomaliaMensagem:
    def test_prefixo_anomalia_na_mensagem(self, db, mock_settings):
        import notifier

        object.__setattr__(mock_settings, "ai_importancia", False)
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
             patch("notifier._disparar_webhooks"):
            notifier.notificar(resultado, _make_edicao())
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
        pubs = [{"tipo": "Portaria"}]
        resultado = _make_resultado(publicacoes=pubs)
        with patch("notifier.SETTINGS", mock_settings):
            assert len(_publicacoes_para_alerta(resultado)) == 1
