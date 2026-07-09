"""Testes do orquestrador unificado (pipeline.py)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from detector import DetectionMetrics, DetectionResult
from downloader import DownloadResult
from scraper import Edicao


def _resultado(edicao_id: int = 1) -> DetectionResult:
    return DetectionResult(
        encontrado=True,
        edicao_id=edicao_id,
        edicao_titulo="Ed Teste",
        paginas_com_mencao=[1],
        trechos=[{"pagina": 1, "trecho": "Inajá"}],
        termos_encontrados=["Inajá"],
        mencoes_db=[{"pagina": 1, "trecho": "Inajá", "termo": "Inajá"}],
        publicacoes=[{"pagina": 1, "categoria": "publicacao_oficial", "trecho": "x"}],
        metricas=DetectionMetrics(
            publicacoes_brutas=1,
            publicacoes_finais=1,
            paginas_total=1,
            mencoes=1,
        ),
    )


def test_processar_edicao_fluxo_ok(db, mock_settings, tmp_path):
    import database
    import pipeline

    pdf = tmp_path / "ed.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    ocr_mock = MagicMock()
    ocr_mock.paginas = []
    ocr_mock.texto_completo = "Inajá"
    ocr_mock.texto_path = tmp_path / "ed.txt"
    ocr_mock.avisos = []
    ocr_mock.texto_path.write_text("Inajá", encoding="utf-8")

    edicao = Edicao(
        url="https://example.com/a.pdf",
        titulo="A",
        data_publicacao="2026-07-01",
    )
    eid = database.insert_or_get_edicao(
        edicao.url, edicao.titulo, edicao.data_publicacao
    )
    download = DownloadResult(
        edicao_id=eid,
        edicao=edicao,
        caminho=pdf,
        tamanho=pdf.stat().st_size,
        md5="abc",
    )

    with (
        patch("pipeline.baixar_edicao", return_value=download),
        patch("pipeline.extrair_texto", return_value=ocr_mock) as mock_ocr,
        patch("pipeline.detectar", return_value=_resultado(eid)),
        patch("pipeline.notificar") as mock_notify,
    ):
        result = pipeline.processar_edicao(edicao, force_ocr=False, fast_ocr=True)

    assert result is not None
    assert result.encontrado is True
    mock_ocr.assert_called_once()
    mock_notify.assert_called_once()


def test_processar_edicao_falha_download(db):
    import pipeline

    edicao = Edicao(
        url="https://example.com/fail.pdf",
        titulo="F",
        data_publicacao=None,
    )
    with patch("pipeline.baixar_edicao", side_effect=RuntimeError("net")):
        result = pipeline.processar_edicao(edicao)
    assert result is None


def test_processar_pendentes_automatico(db, mock_settings, tmp_path):
    import database
    import pipeline

    eid = database.insert_or_get_edicao(
        "https://example.com/pend.pdf", "Pend", "2026-07-01"
    )
    calls = []

    def fake_processar(edicao, **kwargs):
        calls.append(edicao.url)
        return _resultado(kwargs.get("edicao_id") or eid)

    with patch("pipeline.processar_edicao", side_effect=fake_processar):
        n = pipeline.processar_pendentes_automatico(limit=5, recent_days=400)
    assert n >= 1
    assert any("pend.pdf" in u for u in calls)


def test_reprocessar_deteccao_de_cache(db, mock_settings, tmp_path):
    import database
    import pipeline
    from ocr.models import OCRResult, PageText

    pdf = tmp_path / "ed.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    edicao = Edicao(
        url="https://example.com/c.pdf",
        titulo="C",
        data_publicacao="2026-05-28",
    )
    eid = database.insert_or_get_edicao(
        edicao.url, edicao.titulo, edicao.data_publicacao
    )
    database.update_download(eid, pdf, 10, "md5")

    ocr = OCRResult(
        texto_completo="Inajá",
        paginas=[PageText(pagina=1, texto="Inajá", metodo="ocr")],
        texto_path=tmp_path / "ed.txt",
        avisos=[],
    )
    ocr.texto_path.write_text("Inajá", encoding="utf-8")

    with (
        patch("ocr.cache._carregar_cache_ocr", return_value=ocr),
        patch("pipeline.detectar", return_value=_resultado(eid)),
        patch("pipeline.notificar") as mock_notify,
    ):
        result = pipeline.reprocessar_deteccao_de_cache(
            eid, notificar_se_encontrado=False
        )

    assert result is not None
    assert result.encontrado is True
    mock_notify.assert_not_called()
