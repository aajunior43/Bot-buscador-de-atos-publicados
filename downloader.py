from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import database
from config import SETTINGS
from scraper import Edicao


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    edicao_id: int
    edicao: Edicao
    caminho: Path
    tamanho: int
    md5: str


def _headers() -> dict[str, str]:
    return {"User-Agent": SETTINGS.user_agent, "Accept": "application/pdf,text/html,*/*"}


def _safe_filename(url: str, titulo: str) -> str:
    parsed = urlparse(url)
    nome = unquote(Path(parsed.path).name)
    if not nome or "." not in nome:
        nome = titulo
    nome = re.sub(r"[^A-Za-z0-9_.-]+", "_", nome).strip("_")
    # Corrige anos com dígitos extras no nome (ex: 20266 → 2026)
    nome = re.sub(r"\b(2\d{3})\d+\b", r"\1", nome)
    if not nome.lower().endswith(".pdf"):
        nome = f"{nome or 'edicao'}.pdf"
    return nome


def _destino(edicao: Edicao) -> Path:
    ano, mes = "sem-data", "00"
    if edicao.data_publicacao:
        ano, mes = edicao.data_publicacao[0:4], edicao.data_publicacao[5:7]
    pasta = SETTINGS.download_dir / ano / mes
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / _safe_filename(edicao.url, edicao.titulo)


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validar_pdf(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError(f"Arquivo vazio ou inexistente: {path}")
    with path.open("rb") as fp:
        header = fp.read(5)
    if header != b"%PDF-":
        raise ValueError(f"Arquivo baixado não parece PDF válido: {path}")


def _resolver_pdf_url(url: str, session: requests.Session) -> str:
    resp = session.get(url, headers=_headers(), timeout=SETTINGS.request_timeout, allow_redirects=True)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "").lower()
    if "application/pdf" in content_type or resp.url.lower().split("?")[0].endswith(".pdf"):
        return resp.url

    soup = BeautifulSoup(resp.text, "html.parser")
    seletores = ["a[href]", "iframe[src]", "embed[src]", "object[data]"]
    for seletor in seletores:
        for node in soup.select(seletor):
            attr = "href" if node.name == "a" else "data" if node.name == "object" else "src"
            candidato = node.get(attr, "").strip()
            if ".pdf" in candidato.lower():
                return urljoin(resp.url, candidato)

    raise ValueError(f"Não foi possível localizar PDF em {url}")


def baixar_edicao(edicao: Edicao) -> DownloadResult:
    edicao_id = database.insert_or_get_edicao(
        edicao.url, edicao.titulo, edicao.data_publicacao
    )
    destino = _destino(edicao)

    if destino.exists():
        _validar_pdf(destino)
        md5 = _md5(destino)
        database.update_download(edicao_id, destino, destino.stat().st_size, md5)
        logger.info("PDF já existente, download pulado: %s", destino)
        return DownloadResult(edicao_id, edicao, destino, destino.stat().st_size, md5)

    session = requests.Session()
    ultimo_erro: Exception | None = None
    for tentativa in range(1, SETTINGS.max_retries + 1):
        try:
            pdf_url = (
                edicao.url
                if edicao.url.lower().split("?")[0].endswith(".pdf")
                else _resolver_pdf_url(edicao.url, session)
            )
            with session.get(
                pdf_url,
                headers=_headers(),
                timeout=SETTINGS.request_timeout,
                stream=True,
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                with destino.open("wb") as fp:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            fp.write(chunk)

            _validar_pdf(destino)
            md5 = _md5(destino)
            tamanho = destino.stat().st_size
            database.update_download(edicao_id, destino, tamanho, md5)
            logger.info("PDF baixado: %s (%s bytes)", destino, tamanho)
            return DownloadResult(edicao_id, edicao, destino, tamanho, md5)
        except Exception as exc:
            ultimo_erro = exc
            logger.warning(
                "Falha no download de %s na tentativa %s/%s: %s",
                edicao.url,
                tentativa,
                SETTINGS.max_retries,
                exc,
            )
            if destino.exists():
                destino.unlink(missing_ok=True)
            time.sleep(2 ** (tentativa - 1))

    raise RuntimeError(f"Download falhou para {edicao.url}: {ultimo_erro}")
