from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from config import SETTINGS
import database as db

logger = logging.getLogger(__name__)


def _api_key() -> str:
    key = db.get_setting("opencode_api_key", "")
    if not key:
        key = db.get_setting("openrouter_api_key", "")
    if not key:
        key = SETTINGS.opencode_api_key
    return key


def _headers() -> dict[str, str]:
    key = _api_key()
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


_SYSTEM_PROMPT = """Você é um especialista em análise de publicações oficiais de jornais brasileiros.

Sua tarefa é analisar o texto de uma publicação extraído por OCR (pode conter erros) e extrair dados estruturados.

Responda APENAS com um JSON válido (sem markdown, sem comentários), com estes campos:
- texto_corrigido: string — texto com erros de OCR corrigidos
- orgao: string ou null — órgão responsável pela publicação
- tipo: string ou null — tipo do ato (Decreto, Portaria, Lei, Edital, Aviso, Extrato, etc.)
- numero: string ou null — número do documento
- data_documento: string ou null — data do documento
- assunto: string ou null — assunto/resumo em 1-2 frases
- valor: string ou null — valor monetário se houver (ex: R$ 15.000,00)
- categoria: "publicacao_oficial" | "materia_jornalistica" | "patrocinador_distribuicao"
- resumo: string — resumo de 2-3 linhas do conteúdo
- tem_mencao_inaja: boolean — se a publicação menciona direta ou indiretamente o município de Inajá-PR"""


def _prompt_usuario(trecho: str) -> str:
    return f"Texto OCR:\n\n{trecho}\n\nExtraia os dados no JSON conforme solicitado."


def _tentar_recuperar_json(content: str) -> dict | None:
    """Tenta recuperar JSON truncado (string não terminada) fechando o objeto."""
    tentativas = [
        content,
        content.rstrip() + '"}',
        content.rstrip() + '"}' + "}",
        content.rstrip().rsplit(",", 1)[0] + "}",
    ]
    for tentativa in tentativas:
        try:
            return json.loads(tentativa)
        except json.JSONDecodeError:
            continue
    return None


def _extrair_publicacao(trecho: str, timeout: int) -> dict[str, Any] | None:
    content = ""
    try:
        resp = requests.post(
            "https://opencode.ai/zen/go/v1/chat/completions",
            headers=_headers(),
            json={
                "model": SETTINGS.opencode_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _prompt_usuario(trecho)},
                ],
                "max_tokens": SETTINGS.ai_max_tokens,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"].get("content") or ""
        finish = data["choices"][0].get("finish_reason", "")
        usage = data.get("usage", {})
        if not content.strip():
            logger.warning(
                "Resposta vazia do OpenCode Go (finish_reason=%s, usage=%s). "
                "Aumentando max_tokens pode ajudar.",
                finish,
                usage,
            )
            return None
        logger.info(
            "Resposta IA: finish=%s, tokens=%s, content_len=%s, content_preview=%s",
            finish,
            usage.get("completion_tokens"),
            len(content),
            content[:200],
        )
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "JSON truncado da IA (finish_reason=%s) — tentando recuperação: %s | preview=%s",
                finish,
                exc,
                content[:300],
            )
            recuperado = _tentar_recuperar_json(content)
            if recuperado:
                logger.info("JSON recuperado com sucesso após truncamento")
                return recuperado
            logger.error(
                "Erro ao decodificar resposta JSON do OpenCode Go (recuperação falhou) | "
                "content_recebido=%s",
                content[:500],
            )
            return None
    except requests.Timeout:
        logger.warning("Timeout ao chamar OpenCode Go para trecho de %s chars", len(trecho))
    except json.JSONDecodeError as exc:
        logger.error(
            "Erro JSON do OpenCode Go: %s | content_recebido=%s",
            exc,
            content[:500] if content else "N/A",
        )
    except requests.RequestException:
        logger.exception("Erro na requisição ao OpenCode Go")
    except Exception:
        logger.exception("Erro inesperado ao processar IA")
    return None


def refinar_publicacoes(publicacoes: list[dict]) -> list[dict]:
    key = _api_key()
    if not key or not SETTINGS.ai_refine_publications:
        logger.info("IA desativada: key=%s, refine=%s", bool(key), SETTINGS.ai_refine_publications)
        return publicacoes

    logger.info("Iniciando refinamento IA de %s publicacoes (model=%s, max_tokens=%s, timeout=%ss)",
                len(publicacoes), SETTINGS.opencode_model, SETTINGS.ai_max_tokens, SETTINGS.ai_timeout_seconds)
    refinadas: list[dict | None] = [None] * len(publicacoes)
    timeout = max(10, SETTINGS.ai_timeout_seconds)
    workers = min(4, len(publicacoes))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut = {
            pool.submit(_extrair_publicacao, p.get("trecho", ""), timeout): i
            for i, p in enumerate(publicacoes)
        }
        for future in as_completed(fut):
            i = fut[future]
            ia = future.result()
            pub = dict(publicacoes[i])
            if ia:
                # Se a IA respondeu e confirmou que NÃO menciona Inajá, descarta para evitar falso positivo
                if ia.get("tem_mencao_inaja") is False:
                    refinadas[i] = None
                    continue

                pub["texto_corrigido"] = ia.get("texto_corrigido") or pub.get("trecho")
                pub["resumo_ia"] = ia.get("resumo")
                pub["categoria_ia"] = ia.get("categoria") or pub.get("categoria")
                pub["orgao"] = ia.get("orgao") or pub.get("orgao")
                pub["tipo"] = ia.get("tipo") or pub.get("tipo")
                pub["numero"] = ia.get("numero") or pub.get("numero")
                pub["data_documento"] = ia.get("data_documento") or pub.get("data_documento")
                pub["assunto"] = ia.get("assunto") or pub.get("assunto")
                pub["valor"] = ia.get("valor") or pub.get("valor")
            refinadas[i] = pub

    resultado_final = [r for r in refinadas if r is not None]
    logger.info("IA refinou %s de %s publicações (mantidas: %s)",
                sum(1 for r in resultado_final if r.get("resumo_ia")), len(publicacoes), len(resultado_final))
    return resultado_final
