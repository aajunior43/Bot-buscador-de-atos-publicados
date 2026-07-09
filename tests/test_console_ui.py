"""Testes do terminal rico (funções puras, sem I/O crítico)."""
from __future__ import annotations

import console_ui


def test_bar_and_pct():
    assert console_ui.pct(0, 10) == 0
    assert console_ui.pct(5, 10) == 50
    assert console_ui.pct(10, 10) == 100
    assert len(console_ui.bar(5, 10, width=10)) == 10
    assert "█" in console_ui.bar(10, 10, width=8)


def test_parse_progress_dict():
    cur, tot, label = console_ui.parse_progress_payload(
        {"step": "ocr_fast", "current": 3, "total": 12, "msg": "x"}
    )
    assert cur == 3 and tot == 12
    assert "rápido" in label.lower() or "OCR" in label


def test_parse_progress_string():
    cur, tot, label = console_ui.parse_progress_payload(
        "OCR rápido: Página 4/20 processada"
    )
    assert cur == 4 and tot == 20


def test_session_stats_elapsed():
    s = console_ui.SessionStats()
    assert s.elapsed().endswith("s") or "m" in s.elapsed()


def test_session_eta_and_spark():
    s = console_ui.SessionStats()
    s.duracoes.extend([60.0, 120.0])
    s.historico.extend(["I", ".", "x", "I"])
    assert s.media_seg() == 90.0
    assert s.eta_fila(2) is not None
    spark = s.spark()
    assert "█" in spark and "▒" in spark and "░" in spark


def test_fmt_publicacoes_nao_quebra(capsys):
    console_ui.show_publicacoes(
        [
            {
                "tipo": "Decreto",
                "numero": "1/2026",
                "orgao": "Prefeitura",
                "valor": "R$ 10,00",
                "resumo_ia": "Teste de ato",
                "pagina": 3,
            }
        ]
    )
    out = capsys.readouterr().out
    assert "Decreto" in out
    assert "Prefeitura" in out
    assert "valores" in out.lower() or "R$" in out


def test_phase_rail_and_score(capsys):
    console_ui.phase_reset()
    console_ui.phase_set("DL", "run")
    console_ui.phase_set("DL", "ok")
    console_ui.phase_set("OCR", "run")
    out = capsys.readouterr().out
    assert "DL" in out and "OCR" in out
    console_ui.SESSION.processadas = 2
    console_ui.SESSION.com_inaja = 1
    console_ui.SESSION.publicacoes = 3
    assert console_ui.SESSION.score() > 0
    assert console_ui.SESSION.hit_rate() == 50.0


def test_rich_formatter_skips_column_noise():
    fmt = console_ui.RichConsoleFormatter()
    import logging

    rec = logging.LogRecord(
        name="ocr.tesseract",
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg="Página 10: 5 coluna(s) detectada(s)",
        args=(),
        exc_info=None,
    )
    assert fmt.format(rec) == ""
