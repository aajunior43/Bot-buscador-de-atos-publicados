from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _csv_env(name: str) -> list[str]:
    valor = os.getenv(name, "")
    return [item.strip() for item in valor.split(",") if item.strip()]


def _int_env(name: str, padrao: int) -> int:
    try:
        return int(os.getenv(name, str(padrao)))
    except ValueError:
        return padrao


def _bool_env(name: str, padrao: bool = False) -> bool:
    valor = os.getenv(name)
    if valor is None:
        return padrao
    return valor.strip().casefold() in {"1", "true", "sim", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    site_url: str = os.getenv(
        "SITE_URL", "https://www.oregionaljornal.com.br/edicoes/"
    )
    user_agent: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (compatible; JornalMonitor/1.0; +https://www.oregionaljornal.com.br/)",
    )
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    check_interval_hours: int = _int_env("CHECK_INTERVAL_HOURS", 6)
    ocr_language: str = os.getenv("OCR_LANGUAGE", "por").strip() or "por"
    extra_terms: list[str] = None  # type: ignore[assignment]
    inaja_cep_prefixes: list[str] = None  # type: ignore[assignment]
    ignore_context_terms: list[str] = None  # type: ignore[assignment]
    download_dir: Path = Path(os.getenv("DOWNLOAD_DIR", "./edicoes"))
    alert_dir: Path = Path(os.getenv("ALERT_DIR", "./alertas"))
    log_dir: Path = Path(os.getenv("LOG_DIR", "./logs"))
    db_path: Path = Path(os.getenv("DB_PATH", "./jornal_monitor.db"))
    request_timeout: int = _int_env("REQUEST_TIMEOUT_SECONDS", 45)
    max_retries: int = _int_env("MAX_RETRIES", 3)
    min_text_chars_per_page: int = _int_env("MIN_TEXT_CHARS_PER_PAGE", 100)
    force_ocr: bool = _bool_env("FORCE_OCR", False)
    ocr_dpi: int = _int_env("OCR_DPI", 200)
    ocr_fast_dpi: int = _int_env("OCR_FAST_DPI", 150)
    ocr_timeout_seconds: int = _int_env("OCR_TIMEOUT_SECONDS", 120)
    ocr_fast_timeout_seconds: int = _int_env("OCR_FAST_TIMEOUT_SECONDS", 45)
    ocr_layout_columns: int = _int_env("OCR_LAYOUT_COLUMNS", 3)

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra_terms", _csv_env("INAJA_EXTRA_TERMS"))
        object.__setattr__(self, "inaja_cep_prefixes", _csv_env("INAJA_CEP_PREFIXES"))
        object.__setattr__(
            self,
            "ignore_context_terms",
            _csv_env("INAJA_IGNORE_CONTEXT_TERMS")
            or [
                "distribuição avulsa",
                "distribuicao avulsa",
                "auto posto",
                "panificadora",
                "farmácia",
                "farmacia",
                "loterias",
                "patrocinadores",
                "anunciante",
                "anunciantes",
            ],
        )
        for pasta in (self.download_dir, self.alert_dir, self.log_dir):
            pasta.mkdir(parents=True, exist_ok=True)


SETTINGS = Settings()
