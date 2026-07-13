"""
Fixtures e configuração global de testes.
Usa banco de dados SQLite em memória para isolamento completo.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# Garante que o root do projeto está no path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def mock_settings(tmp_path):
    """Sobrescreve SETTINGS para usar diretórios temporários e banco em memória."""
    # Isola o lock de arquivo do BOT em execução (evita hang de 600s nos testes)
    lock_path = tmp_path / "processamento.lock"
    os.environ["PROCESS_LOCK_PATH"] = str(lock_path)
    try:
        import process_lock as _pl

        _pl.DEFAULT_LOCK = lock_path
    except Exception:
        pass

    from config import Settings
    settings = Settings.__new__(Settings)
    # Não usar __post_init__ para evitar criar pastas de verdade
    object.__setattr__(settings, "site_url", "https://example.com/edicoes/")
    object.__setattr__(settings, "user_agent", "TestAgent/1.0")
    object.__setattr__(settings, "check_interval_hours", 6)
    object.__setattr__(settings, "ocr_language", "por")
    object.__setattr__(settings, "extra_terms", ["Nome Teste"])
    object.__setattr__(settings, "inaja_cep_prefixes", ["87670"])
    object.__setattr__(settings, "ignore_context_terms", ["distribuição avulsa", "farmácia"])
    object.__setattr__(settings, "download_dir", tmp_path / "edicoes")
    object.__setattr__(settings, "alert_dir", tmp_path / "alertas")
    object.__setattr__(settings, "log_dir", tmp_path / "logs")
    object.__setattr__(settings, "atos_dir", tmp_path / "atos")
    object.__setattr__(settings, "atos_espelhar", True)
    object.__setattr__(settings, "atos_por_tipo", True)
    object.__setattr__(settings, "atos_por_orgao", True)
    object.__setattr__(settings, "atos_exportar_midia", False)
    object.__setattr__(settings, "atos_pagina_dpi", 150)
    object.__setattr__(settings, "db_path", tmp_path / "test.db")
    object.__setattr__(settings, "request_timeout", 10)
    object.__setattr__(settings, "max_retries", 1)
    object.__setattr__(settings, "min_text_chars_per_page", 100)
    object.__setattr__(settings, "force_ocr", False)
    object.__setattr__(settings, "ocr_dpi", 150)
    object.__setattr__(settings, "ocr_retry_dpi", 200)
    object.__setattr__(settings, "ocr_max_dimension", 3200)
    object.__setattr__(settings, "ocr_fast_dpi", 100)
    object.__setattr__(settings, "ocr_fast_min_chars", 300)
    object.__setattr__(settings, "ocr_fast_max_dimension", 1800)
    object.__setattr__(settings, "ocr_timeout_seconds", 30)
    object.__setattr__(settings, "ocr_fast_timeout_seconds", 20)
    object.__setattr__(settings, "ocr_layout_columns", 3)
    object.__setattr__(settings, "opencode_api_key", "")
    object.__setattr__(settings, "opencode_model", "test-model")
    object.__setattr__(settings, "ai_refine_publications", False)
    object.__setattr__(settings, "ai_timeout_seconds", 5)
    object.__setattr__(settings, "ai_max_tokens", 100)
    object.__setattr__(settings, "ai_importancia", True)
    object.__setattr__(settings, "ai_importancia_min_notificar", 3)
    object.__setattr__(settings, "ai_resumo_diario", False)
    object.__setattr__(settings, "ai_explicacao", False)
    object.__setattr__(settings, "ai_explicacao_auto", False)
    object.__setattr__(settings, "ai_auditoria_so_mencao", False)
    object.__setattr__(settings, "ai_chat", False)
    object.__setattr__(settings, "ai_anomalia", True)
    object.__setattr__(settings, "ai_triagem_lote", False)
    object.__setattr__(settings, "ai_partes", True)
    object.__setattr__(settings, "ai_checklist", True)
    object.__setattr__(settings, "ai_temas", True)
    object.__setattr__(settings, "ai_ocr_contextual", True)
    object.__setattr__(settings, "ai_anti_alucinacao", True)
    object.__setattr__(settings, "ai_fn_recuperacao", False)
    object.__setattr__(settings, "ai_similares", False)
    object.__setattr__(settings, "ai_timeline", False)
    object.__setattr__(settings, "ai_max_calls_por_ciclo", 80)
    object.__setattr__(settings, "quality_fix_numero_ano", True)
    object.__setattr__(settings, "quality_ano_max_futuro", 1)
    object.__setattr__(settings, "quality_confianca", True)
    object.__setattr__(settings, "quality_confianca_alta_min", 85)
    object.__setattr__(settings, "quality_confianca_media_min", 55)
    object.__setattr__(settings, "quality_re_ia_auto", True)
    object.__setattr__(settings, "quality_re_ia_max_tentativas", 3)
    object.__setattr__(settings, "quality_re_ia_espelhar", False)
    object.__setattr__(settings, "agente_max_re_ia_por_ciclo", 5)
    object.__setattr__(settings, "agente_max_re_ia_por_dia", 40)
    object.__setattr__(settings, "absence_alert_days", 30)
    object.__setattr__(settings, "webhook_url", "")
    object.__setattr__(settings, "max_edicoes_por_ciclo", 20)
    object.__setattr__(settings, "auto_process", True)
    object.__setattr__(settings, "auto_process_limit", 3)
    object.__setattr__(settings, "auto_process_max_por_ciclo", 10)
    object.__setattr__(settings, "auto_process_continuo", True)
    object.__setattr__(settings, "auto_process_dias", 365)
    object.__setattr__(settings, "auto_process_desde", "")
    object.__setattr__(settings, "auto_process_max_falhas", 3)
    object.__setattr__(settings, "web_scan_interval_hours", 6)
    object.__setattr__(settings, "webapp_user", "")
    object.__setattr__(settings, "webapp_password", "")
    object.__setattr__(settings, "require_webapp_auth", False)
    object.__setattr__(settings, "app_env", "development")
    object.__setattr__(settings, "opencode_api_url", "https://example.com/v1/chat/completions")
    object.__setattr__(settings, "poppler_path", "")
    object.__setattr__(settings, "tesseract_path", "")
    object.__setattr__(settings, "ocr_max_workers", 1)
    object.__setattr__(settings, "ocr_min_workers", 1)
    object.__setattr__(settings, "ocr_adaptive_cpu", False)
    object.__setattr__(settings, "ocr_cpu_target", 0.88)
    for pasta in (settings.download_dir, settings.alert_dir, settings.log_dir):
        pasta.mkdir(parents=True, exist_ok=True)

    with patch("config.SETTINGS", settings):
        import database
        import importlib
        importlib.reload(database)
        with patch("database.SETTINGS", settings), \
             patch("detector.SETTINGS", settings), \
             patch("webapp.SETTINGS", settings, create=True), \
             patch("pipeline.SETTINGS", settings, create=True):
            yield settings


@pytest.fixture
def db(mock_settings):
    """Inicializa o banco de dados de teste e retorna o path."""
    import database
    database.init_db()
    return mock_settings.db_path
