from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import database
from config import SETTINGS


logger = logging.getLogger(__name__)
DATE_RE = re.compile(r"\b(\d{2})[-/](\d{2})[-/](\d{4})\b")


@dataclass(frozen=True)
class Edicao:
    url: str
    titulo: str
    data_publicacao: str | None


def _headers() -> dict[str, str]:
    return {"User-Agent": SETTINGS.user_agent, "Accept": "text/html,application/pdf"}


def _normalizar_data(texto: str) -> str | None:
    match = DATE_RE.search(texto)
    if not match:
        return None
    dia, mes, ano = match.groups()
    try:
        return datetime(int(ano), int(mes), int(dia)).date().isoformat()
    except ValueError:
        return None


def _extrair_com_bs4(html: str, base_url: str) -> list[Edicao]:
    soup = BeautifulSoup(html, "html.parser")
    edicoes: dict[str, Edicao] = {}
    for link in soup.select("a[href]"):
        href = link.get("href", "").strip()
        texto = " ".join(link.get_text(" ", strip=True).split())
        url = urljoin(base_url, href)
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        data = _normalizar_data(texto) or _normalizar_data(url)
        parece_edicao = data is not None or ".pdf" in url.lower()
        if not parece_edicao:
            continue

        titulo = texto or f"Edição {data or url.rsplit('/', 1)[-1]}"
        edicoes[url] = Edicao(url=url, titulo=titulo, data_publicacao=data)

    return sorted(
        edicoes.values(),
        key=lambda item: item.data_publicacao or "",
        reverse=True,
    )


def _extrair_com_playwright(url: str) -> list[Edicao]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Playwright não instalado; seguindo sem fallback JS.")
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=SETTINGS.user_agent)
            page.goto(url, wait_until="networkidle", timeout=SETTINGS.request_timeout * 1000)
            html = page.content()
            browser.close()
        return _extrair_com_bs4(html, url)
    except Exception:
        logger.exception("Falha ao coletar edições com Playwright.")
        return []


def coletar_edicoes() -> list[Edicao]:
    """Coleta edições com retry exponencial para maior resiliência."""
    logger.info("Coletando edições em %s", SETTINGS.site_url)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, SETTINGS.max_retries + 1):
        try:
            resp = requests.get(
                SETTINGS.site_url,
                headers=_headers(),
                timeout=SETTINGS.request_timeout,
            )
            resp.raise_for_status()
            edicoes = _extrair_com_bs4(resp.text, SETTINGS.site_url)
            if not edicoes:
                logger.warning(
                    "Nenhuma edição encontrada via BS4 (possível mudança de layout). "
                    "Tentando Playwright..."
                )
                edicoes = _extrair_com_playwright(SETTINGS.site_url)
            if not edicoes:
                logger.error(
                    "Nenhuma edição encontrada em %s — verifique se o layout do site mudou.",
                    SETTINGS.site_url,
                )
            logger.info("Edições encontradas: %s", len(edicoes))
            return edicoes
        except requests.RequestException as exc:
            ultimo_erro = exc
            espera = 2 ** (tentativa - 1)
            logger.warning(
                "Falha ao coletar edições (tentativa %s/%s): %s. Aguardando %ss...",
                tentativa,
                SETTINGS.max_retries,
                exc,
                espera,
            )
            time.sleep(espera)

    logger.error("Todas as tentativas de coleta falharam: %s", ultimo_erro)
    return []


def listar_edicoes(force_rescan: bool = False) -> list[Edicao]:
    edicoes = coletar_edicoes()
    novas = [
        edicao
        for edicao in edicoes
        if force_rescan or not database.url_exists(edicao.url)
    ]
    logger.info("Edições encontradas: %s; novas: %s", len(edicoes), len(novas))
    return novas
