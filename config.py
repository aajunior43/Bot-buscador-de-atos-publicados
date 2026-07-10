from __future__ import annotations

import os
from dataclasses import dataclass, field
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


def _float_env(name: str, padrao: float) -> float:
    try:
        return float(os.getenv(name, str(padrao)))
    except ValueError:
        return padrao


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
    extra_terms: list[str] = field(default_factory=list)
    inaja_cep_prefixes: list[str] = field(default_factory=list)
    ignore_context_terms: list[str] = field(default_factory=list)
    download_dir: Path = Path(os.getenv("DOWNLOAD_DIR", "./edicoes"))
    alert_dir: Path = Path(os.getenv("ALERT_DIR", "./alertas"))
    log_dir: Path = Path(os.getenv("LOG_DIR", "./logs"))
    # Pasta espelhada de atos oficiais (ano/mês/dia + atalhos tipo/órgão)
    atos_dir: Path = Path(os.getenv("ATOS_DIR", "./atos"))
    atos_espelhar: bool = _bool_env("ATOS_ESPELHAR", True)
    atos_por_tipo: bool = _bool_env("ATOS_POR_TIPO", True)
    atos_por_orgao: bool = _bool_env("ATOS_POR_ORGAO", True)
    # Mídia da página do jornal (PNG qualidade + PDF só da página)
    atos_exportar_midia: bool = _bool_env("ATOS_EXPORTAR_MIDIA", True)
    atos_pagina_dpi: int = _int_env("ATOS_PAGINA_DPI", 200)
    db_path: Path = Path(os.getenv("DB_PATH", "./jornal_monitor.db"))
    request_timeout: int = _int_env("REQUEST_TIMEOUT_SECONDS", 45)
    max_retries: int = _int_env("MAX_RETRIES", 3)
    min_text_chars_per_page: int = _int_env("MIN_TEXT_CHARS_PER_PAGE", 100)
    force_ocr: bool = _bool_env("FORCE_OCR", False)
    ocr_dpi: int = _int_env("OCR_DPI", 200)
    ocr_retry_dpi: int = _int_env("OCR_RETRY_DPI", 250)
    ocr_max_dimension: int = _int_env("OCR_MAX_DIMENSION", 3200)
    ocr_fast_dpi: int = _int_env("OCR_FAST_DPI", 120)
    ocr_fast_min_chars: int = _int_env("OCR_FAST_MIN_CHARS", 300)
    ocr_fast_max_dimension: int = _int_env("OCR_FAST_MAX_DIMENSION", 1800)
    ocr_timeout_seconds: int = _int_env("OCR_TIMEOUT_SECONDS", 120)
    ocr_fast_timeout_seconds: int = _int_env("OCR_FAST_TIMEOUT_SECONDS", 120)
    ocr_layout_columns: int = _int_env("OCR_LAYOUT_COLUMNS", 3)
    opencode_api_key: str = os.getenv("OPENCODE_API_KEY", "").strip()
    opencode_api_url: str = os.getenv(
        "OPENCODE_API_URL", "https://opencode.ai/zen/go/v1/chat/completions"
    ).strip()
    opencode_model: str = os.getenv("OPENCODE_MODEL", "deepseek-v4-flash").strip()
    ai_refine_publications: bool = _bool_env("AI_REFINE_PUBLICATIONS", True)
    ai_timeout_seconds: int = _int_env("AI_TIMEOUT_SECONDS", 30)
    ai_max_tokens: int = _int_env("AI_MAX_TOKENS", 8000)
    # Funções extras de IA (consumo ainda baixo)
    ai_importancia: bool = _bool_env("AI_IMPORTANCIA", True)
    ai_importancia_min_notificar: int = _int_env("AI_IMPORTANCIA_MIN_NOTIFICAR", 3)
    ai_resumo_diario: bool = _bool_env("AI_RESUMO_DIARIO", True)
    ai_explicacao: bool = _bool_env("AI_EXPLICACAO", True)
    ai_explicacao_auto: bool = _bool_env("AI_EXPLICACAO_AUTO", False)
    ai_auditoria_so_mencao: bool = _bool_env("AI_AUDITORIA_SO_MENCAO", True)
    ai_chat: bool = _bool_env("AI_CHAT", True)
    ai_max_calls_por_ciclo: int = _int_env("AI_MAX_CALLS_POR_CICLO", 50)
    # SMTP / E-mail
    smtp_host: str = os.getenv("SMTP_HOST", "").strip()
    smtp_port: int = _int_env("SMTP_PORT", 587)
    smtp_user: str = os.getenv("SMTP_USER", "").strip()
    smtp_pass: str = os.getenv("SMTP_PASS", "").strip()
    smtp_to: str = os.getenv("SMTP_TO", "").strip()
    smtp_from: str = os.getenv("SMTP_FROM", "").strip()
    notify_email_always: bool = _bool_env("NOTIFY_EMAIL_ALWAYS", False)
    # Alerta de ausência
    absence_alert_days: int = _int_env("ABSENCE_ALERT_DAYS", 30)
    # Webhook genérico
    webhook_url: str = os.getenv("WEBHOOK_URL", "").strip()
    # Limite de edições novas processadas por ciclo (evita sobrecarga no primeiro uso)
    max_edicoes_por_ciclo: int = _int_env("MAX_EDICOES_POR_CICLO", 10)
    # Automação total: processa OCR/notificação sem clique manual
    auto_process: bool = _bool_env("AUTO_PROCESS", True)
    # Quantas edições pendentes por *lote* (evita lock longo demais)
    auto_process_limit: int = _int_env("AUTO_PROCESS_LIMIT", 5)
    # Máximo de edições da fila no ciclo completo (0 = sem teto extra, só o lote)
    # Com AUTO_PROCESS_CONTINUO o BOT esvazia a fila em vários lotes entre ciclos.
    auto_process_max_por_ciclo: int = _int_env("AUTO_PROCESS_MAX_POR_CICLO", 40)
    # Entre ciclos de 6h, continua processando a fila (um lote por vez)
    auto_process_continuo: bool = _bool_env("AUTO_PROCESS_CONTINUO", True)
    # Só auto-processa edições com data nos últimos N dias (0 = sem esse filtro)
    auto_process_dias: int = _int_env("AUTO_PROCESS_DIAS", 120)
    # Data mínima inclusiva (YYYY-MM-DD). Ex.: 2020-01-01 = não processa 2011–2019.
    # Vazio = sem piso de data.
    auto_process_desde: str = os.getenv("AUTO_PROCESS_DESDE", "").strip()
    # Após N falhas de download/OCR a edição sai da fila (quarentena)
    auto_process_max_falhas: int = _int_env("AUTO_PROCESS_MAX_FALHAS", 3)
    # Intervalo do scheduler da web (horas entre varreduras; padrão 6 = 4x/dia)
    web_scan_interval_hours: int = _int_env("WEB_SCAN_INTERVAL_HOURS", 6)
    # Poppler (para pdf2image no Windows)
    poppler_path: str = os.getenv("POPPLER_PATH", "").strip()
    # Tesseract OCR (para pytesseract no Windows)
    tesseract_path: str = os.getenv("TESSERACT_PATH", "").strip()
    # Teto de workers do OCR. 0 = automático (cores-1, deixa 1 núcleo livre)
    ocr_max_workers: int = _int_env("OCR_MAX_WORKERS", 0)
    # Piso de workers (1 = pode reduzir se a CPU saturar)
    ocr_min_workers: int = _int_env("OCR_MIN_WORKERS", 1)
    # Ajusta workers medindo CPU (conservador)
    ocr_adaptive_cpu: bool = _bool_env("OCR_ADAPTIVE_CPU", True)
    # Alvo ~70% (cap interno 80% — não mira 100%)
    ocr_cpu_target: float = _float_env("OCR_CPU_TARGET", 0.70)
    # Autenticação da interface web (HTTP Basic). Se ambos vazios, o webapp
    # fica aberto e um aviso é emitido no log de inicialização.
    webapp_user: str = os.getenv("WEBAPP_USER", "").strip()
    webapp_password: str = os.getenv("WEBAPP_PASSWORD", "").strip()
    # Se true, o webapp recusa subir sem WEBAPP_USER e WEBAPP_PASSWORD.
    # Ative em produção (Docker/Traefik/host público).
    require_webapp_auth: bool = _bool_env("REQUIRE_WEBAPP_AUTH", False)
    # production | development — production implica require_webapp_auth.
    app_env: str = (os.getenv("APP_ENV", "development") or "development").strip().lower()


    def __post_init__(self) -> None:
        # Campos de lista lidos do ambiente (default_factory já garante [] se não chamado)
        if not self.extra_terms:
            object.__setattr__(self, "extra_terms", _csv_env("INAJA_EXTRA_TERMS"))
        if not self.inaja_cep_prefixes:
            object.__setattr__(self, "inaja_cep_prefixes", _csv_env("INAJA_CEP_PREFIXES"))
        if not self.ignore_context_terms:
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


# Municípios vizinhos de Inajá-PR — publicações dessas cidades não devem ser
# atribuídas a Inajá. Lista normalizada (sem acentos, minúscula) para comparação.
MUNICIPIOS_VIZINHOS = [
    "jardim olinda",
    "cruzeiro do sul",
    "santo inacio",
    "florai",
    "paranapoema",
    "itaguaje",
    "colorado",
    "paranacity",
    "loanda",
    "querencia do norte",
    "santa isabel do ivai",
    "marilena",
    "guaira",
    "esperanca nova",
    "altamira do parana",
    "nova londrina",
    "santa cruz de monte castelo",
    "pioneiro jayme canet",
    # Outros da região frequentemente no O Regional
    "uniflor",
    "ourizona",
    "nova esperanca",
    "paraiso do norte",
    "tapejara",
    "cianorte",
    "mandaguacu",
    "mandaguari",
    "maringa",
    "sarandi",
    "paicandu",
    "astorga",
    "presidente castelo branco",
]

# Ensure fully normalized (no accents, lowercase) at import time
def _normalize_municipio(m):
    import unicodedata
    n = unicodedata.normalize("NFKD", m)
    return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()

MUNICIPIOS_VIZINHOS = [_normalize_municipio(m) for m in MUNICIPIOS_VIZINHOS]

# CNPJs oficiais de Inajá-PR (prefixos sem formatação extra)
CNPJ_INAJA_PREFIXES = (
    "75771400",   # 75.771.400/0001-48 — Prefeitura
    "76970318",   # 76.970.318/0001-67 — Município/Câmara
)
