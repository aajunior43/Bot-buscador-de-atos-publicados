from __future__ import annotations

import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from config import SETTINGS, MUNICIPIOS_VIZINHOS
import database as db

logger = logging.getLogger(__name__)

# Circuit breaker: após 401/AuthError, não dispara dezenas de requisições no mesmo processo.
_auth_bloqueada: bool = False
_auth_aviso_emitido: bool = False


def _api_key() -> str:
    key = db.get_setting("opencode_api_key", "")
    if not key:
        key = db.get_setting("openrouter_api_key", "")
    if not key:
        key = SETTINGS.opencode_api_key
    return (key or "").strip()


def _headers() -> dict[str, str]:
    key = _api_key()
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _marcar_auth_invalida(detalhe: str = "") -> None:
    """Bloqueia novas chamadas de IA neste processo após chave inválida."""
    global _auth_bloqueada, _auth_aviso_emitido
    _auth_bloqueada = True
    if _auth_aviso_emitido:
        return
    _auth_aviso_emitido = True
    logger.error(
        "IA desativada neste processo: API key inválida/expirada (401). "
        "Renove OPENCODE_API_KEY no .env ou em /admin e reinicie o serviço. "
        "Detalhe: %s | URL=%s modelo=%s",
        detalhe or "Unauthorized",
        SETTINGS.opencode_api_url,
        SETTINGS.opencode_model,
    )


def reset_auth_circuit() -> None:
    """Permite nova tentativa após o usuário atualizar a chave (ex.: admin)."""
    global _auth_bloqueada, _auth_aviso_emitido
    _auth_bloqueada = False
    _auth_aviso_emitido = False


def ia_disponivel() -> bool:
    """True se refine está ligado, há chave e o circuit breaker não disparou."""
    return bool(
        SETTINGS.ai_refine_publications
        and _api_key()
        and not _auth_bloqueada
    )


_SYSTEM_PROMPT_TEMPLATE = """Você é um especialista em análise de publicações oficiais do jornal "O Regional" (Norte do Paraná), com foco no município de Inajá-PR.

Sua tarefa é analisar o texto de UMA publicação extraído por OCR (pode conter erros) e extrair dados estruturados de forma extremamente rigorosa e sem alucinações.

CONTEXTO IMPORTANTE — Município de Inajá-PR:
- CEP local: 87670-000
- Prefeito atual: João Eder Aguilar
- CNPJs que aparecem em documentos de Inajá: 75.771.400/0001-48 e 76.970.318/0001-67
- Cidades VIZINHAS (NÃO são Inajá): {vizinhos}.
- ATENÇÃO ao CNPJ: o número 76.970.318/0001-67 aparece em documentos tanto da Prefeitura quanto da Câmara Municipal. Para diferenciá-los, observe o TEXTO: se o documento começa com "CÂMARA MUNICIPAL DE INAJÁ" ou menciona "CÂMARA", o órgão é a Câmara; se começa com "PREFEITURA MUNICIPAL DE INAJÁ" ou "MUNICÍPIO DE INAJÁ", é a Prefeitura/Município (mesmo que o CNPJ 76.970.318 seja citado).

REGRAS CRÍTICAS DE EXTRAÇÃO:
1. Analise apenas a publicação principal do trecho. Se houver duas publicações misturadas, extraia apenas a primeira/mais proeminente e ignore o restante no resumo.
2. O campo "pertence_a_inaja" deve ser true APENAS se o órgão for da Prefeitura/Câmara/Município de Inajá. Se o texto mencionar outra cidade como órgão (ex: "Prefeitura Municipal de Cruzeiro do Sul"), retorne false.
3. EXTRAÇÃO DE CAMPOS OBRIGATÓRIOS:
   - "numero": busque "Nº", "nº", "N.", "n." seguido de dígitos (ex: "Decreto Nº 042/2026" → numero="042/2026"). Mesmo em "Lei nº 19" ou "Portaria n. 865/2026". Não invente números.
   - "data_documento": busque datas no texto no formato "DD de MÊS de AAAA" ou "DD/MM/AAAA" e normalize para "DD/MM/AAAA". Toda publicação oficial costuma ter uma data — procure-a ativamente.
   - "valor": busque valores "R$ X.XXX,XX" no texto da publicação. Para tipos "Dispensa", "Contrato", "Termo de Homologação", "Extrato de Contrato", geralmente há um valor. Só retorne null se realmente nenhum valor monetário for citado no texto da publicação. Não confunda valores monetários com números de processo, CPF, CNPJ ou datas.
4. Nunca retorne valor corrupto ou alucinado: se o texto diz "R$ 19.876,00", NUNCA retorne "R$ 220" nem parte do valor. O valor deve ser o completo, lido diretamente da expressão "R$ ...".
5. Responda APENAS com um objeto JSON válido. Nunca inclua blocos markdown ```json ... ```, comentários ou explicações extras antes ou depois do JSON.

Campos do JSON de retorno:
- texto_corrigido: string — texto com erros de OCR corrigidos
- orgao: string ou null — órgão responsável (ex: "Prefeitura Municipal de Inajá", "Câmara Municipal de Inajá", "Município de Inajá")
- tipo: string ou null — tipo do ato (Decreto, Portaria, Lei, Edital, Aviso, Extrato, Errata, Notificação, Termo Aditivo, Dispensa de Licitação, Demonstrativo, Relatório Fiscal, RGF, RREO, Balanço, etc.)
- numero: string ou null — número do documento (ex: "042/2026")
- data_documento: string ou null — data do documento no formato DD/MM/AAAA
- assunto: string ou null — assunto/objeto em 1-2 frases
- valor: string ou null — valor monetário no formato "R$ 15.000,00"
- categoria: "publicacao_oficial" | "materia_jornalistica" | "patrocinador_distribuicao"
- resumo: string — resumo objetivo de 2-3 linhas sobre o conteúdo da publicação
- pertence_a_inaja: boolean — se pertence diretamente ao município de Inajá-PR
- tem_mencao_inaja: boolean — se menciona direta ou indiretamente Inajá-PR
- tags: array de strings — até 3 marcadores curtos (ex: ["licitação", "obra", "saúde"])"""


def _prompt_usuario(trecho: str) -> str:
    return f"Texto OCR:\n\n{trecho}\n\nExtraia os dados no JSON conforme solicitado."


def _tentar_recuperar_json(content: str) -> dict | None:
    """Tenta recuperar JSON truncado (string não terminada) fechando o objeto."""
    tentativas = [
        content,
        content.rstrip() + '"}',
        content.rstrip() + '"}' + "}",
        content.rstrip().rsplit(",", 1)[0] + "}",
        re.sub(r'[^}]*$', '}', content.rstrip()),  # close any open
    ]
    for tentativa in tentativas:
        try:
            return json.loads(tentativa)
        except json.JSONDecodeError:
            continue
    return None


def _sem_acentos(texto: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", texto or "")
        if not unicodedata.combining(ch)
    )


def _limpar_trecho_ocr(trecho: str) -> str:
    """Pré-processa o trecho OCR antes de enviar à IA: remove ruídos e normaliza."""
    if not trecho:
        return ""
    texto = trecho
    # Remove caracteres de controle e zeros de largura
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b-\u200f\ufeff]", "", texto)
    # Normaliza espaços excessivos dentro de linhas
    texto = re.sub(r"[ \t]+", " ", texto)
    # Remove linhas vazias consecutivas (mantém no máximo 2 quebras)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    # Correções de OCR comuns já conhecidas
    texto = texto.replace("LEINº", "LEI Nº")
    texto = re.sub(r"\bLEI\s*N[º°O.]", "LEI Nº", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bPORTARIAN[º°O.]", "PORTARIA Nº", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bDECRETON[º°O.]", "DECRETO Nº", texto, flags=re.IGNORECASE)
    # Limita o tamanho para não estourar o contexto da IA
    return texto.strip()[:4000]


def _extrair_publicacao(trecho: str, timeout: int) -> dict[str, Any] | None:
    if _auth_bloqueada:
        return None
    trecho_limpo = _limpar_trecho_ocr(trecho)
    content = ""
    try:
        resp = requests.post(
            SETTINGS.opencode_api_url,
            headers=_headers(),
            json={
                "model": SETTINGS.opencode_model,
                "messages": [
                    {
                        "role": "system",
                        "content": _SYSTEM_PROMPT_TEMPLATE.format(
                            vizinhos=", ".join(m.title() for m in MUNICIPIOS_VIZINHOS)
                        ),
                    },
                    {"role": "user", "content": _prompt_usuario(trecho_limpo)},
                ],
                "max_tokens": SETTINGS.ai_max_tokens,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        if resp.status_code in (401, 403):
            try:
                body = resp.json()
                msg = (
                    body.get("error", {}).get("message")
                    or body.get("message")
                    or resp.text[:200]
                )
            except Exception:
                msg = resp.text[:200]
            _marcar_auth_invalida(f"HTTP {resp.status_code}: {msg}")
            return None
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
        logger.warning(
            "Timeout ao chamar OpenCode Go para trecho de %s chars", len(trecho)
        )
    except json.JSONDecodeError as exc:
        logger.error(
            "Erro JSON do OpenCode Go: %s | content_recebido=%s",
            exc,
            content[:500] if content else "N/A",
        )
    except requests.RequestException as exc:
        # Fallback se status não veio no resp (rede etc.)
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            _marcar_auth_invalida(str(exc))
        else:
            logger.warning("Erro na requisição ao OpenCode Go: %s", exc)
    except Exception:
        logger.exception("Erro inesperado ao processar IA para trecho")
    return None


def _normalizar_valor_ia(valor: str | None) -> str | None:
    """Garante formato consistente R$ X.XXX,XX."""
    if not valor:
        return None
    v = valor.strip()
    if not v:
        return None
    # Remove "valor de", "no valor de" etc.
    v = re.sub(r"^.*?(R\s*[$S])", r"\1", v, flags=re.IGNORECASE) if re.search(r"R\s*[$S]", v, re.IGNORECASE) else v
    # Normaliza prefixo
    v = re.sub(r"^R\s*[$S]\s*", "R$ ", v, flags=re.IGNORECASE)
    return v.strip(".,;:") or None


def _normalizar_data_ia(data: str | None) -> str | None:
    """Tenta normalizar data para DD/MM/AAAA."""
    if not data:
        return None
    d = data.strip()
    if not d:
        return None
    meses = {
        "janeiro": "01", "fevereiro": "02", "março": "03", "marco": "03",
        "abril": "04", "maio": "05", "junho": "06", "julho": "07",
        "agosto": "08", "setembro": "09", "outubro": "10", "novembro": "11", "dezembro": "12",
    }
    m = re.match(r"(\d{1,2})\s+de\s+([a-zçãéíóú]+)\s+de\s+(\d{4})", d, re.IGNORECASE)
    if m:
        dia = m.group(1).zfill(2)
        mes = meses.get(_sem_acentos(m.group(2)).lower())
        if mes:
            return f"{dia}/{mes}/{m.group(3)}"
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", d)
    if m:
        dia = m.group(1).zfill(2)
        mes = m.group(2).zfill(2)
        ano = m.group(3)
        if len(ano) == 2:
            ano = "20" + ano
        return f"{dia}/{mes}/{ano}"
    return d[:40] or None


def _normalizar_orgao_ia(orgao: str | None) -> str | None:
    """Padroniza nomes de órgãos de Inajá."""
    if not orgao:
        return None
    o = orgao.strip()
    o_clean = _sem_acentos(o).casefold()
    if "camara" in o_clean and "inaja" in o_clean:
        return "Câmara Municipal de Inajá"
    if "prefeitura" in o_clean and "inaja" in o_clean:
        return "Prefeitura Municipal de Inajá"
    if o_clean.startswith("municipio de inaja"):
        return "Município de Inajá"
    return o or None


# Mapeamento de variantes de tipo → rótulo canônico (ordem: mais específico primeiro).
_TIPO_CANONICO: list[tuple[str, str]] = [
    ("termo de homologacao e adjudicacao", "Homologação/Adjudicação"),
    ("homologacao e adjudicacao", "Homologação/Adjudicação"),
    ("termo de homologacao", "Homologação/Adjudicação"),
    ("homologacao", "Homologação/Adjudicação"),
    ("adjudicacao", "Homologação/Adjudicação"),
    ("extrato de termo de aditivo", "Termo Aditivo"),
    ("extrato de termo aditivo", "Termo Aditivo"),
    ("termo de aditivo", "Termo Aditivo"),
    ("termo aditivo", "Termo Aditivo"),
    ("extrato de termo de rescisao", "Extrato de Rescisão"),
    ("extrato de contrato", "Extrato de Contrato"),
    ("aviso de licitacao", "Aviso de Licitação"),
    ("chamamento publico", "Chamamento Público"),
    ("concorrencia eletronica", "Concorrência"),
    ("concorrencia", "Concorrência"),
    ("dispensa de licitacao", "Dispensa"),
    ("dispensa eletronica", "Dispensa"),
    ("dispensa", "Dispensa"),
    ("inexigibilidade", "Inexigibilidade"),
    ("relatorio de gestao fiscal", "RGF"),
    ("demonstrativo", "Demonstrativo"),
    ("rreo", "RREO"),
    ("rgf", "RGF"),
    ("portaria", "Portaria"),
    ("decreto", "Decreto"),
    ("edital", "Edital"),
    ("resolucao", "Resolução"),
    ("notificacao", "Notificação"),
    ("errata", "Errata"),
    ("contrato", "Contrato"),
    ("lei", "Lei"),
    ("ato administrativo", "Ato"),
    # "ato" isolado só se a string inteira for curta (evita capturar lixo OCR)
]


def _tipo_e_ato_generico(chave: str) -> bool:
    return chave in {"ato", "ato administrativo"} or chave.startswith("ato ")


def normalizar_tipo_ato(tipo: str | None) -> str | None:
    """Padroniza rótulos de tipo de ato (IA e heurísticas)."""
    if not tipo:
        return None
    bruto = " ".join(str(tipo).split()).strip()
    if not bruto:
        return None
    chave = _sem_acentos(bruto).casefold()
    for padrao, canonico in _TIPO_CANONICO:
        if chave == padrao or chave.startswith(padrao + " ") or padrao + " " in chave + " ":
            # Exige que o padrão apareça como token inicial ou frase reconhecida
            if padrao in chave:
                return canonico
    if _tipo_e_ato_generico(chave):
        return "Ato"
    # Title-case genérico para tipos desconhecidos curtos
    if len(bruto) <= 40:
        return bruto.title()
    return bruto[:60]


def refinar_publicacoes(publicacoes: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Refina publicações via IA.

    Returns:
        (lista mantida, estatísticas de descarte).
        stats: descartes_ia, descartes_vizinho
    """
    stats: dict[str, int] = {"descartes_ia": 0, "descartes_vizinho": 0}
    if not SETTINGS.ai_refine_publications:
        logger.info("IA desativada: AI_REFINE_PUBLICATIONS=false")
        return publicacoes, stats
    if not _api_key():
        logger.info("IA desativada: OPENCODE_API_KEY vazia (.env e settings)")
        return publicacoes, stats
    if _auth_bloqueada:
        logger.debug(
            "IA ignorada: circuit breaker de autenticação ativo (chave inválida)."
        )
        return publicacoes, stats

    logger.info(
        "Iniciando refinamento IA de %s publicacoes (model=%s, max_tokens=%s, timeout=%ss)",
        len(publicacoes),
        SETTINGS.opencode_model,
        SETTINGS.ai_max_tokens,
        SETTINGS.ai_timeout_seconds,
    )
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
                # Se a IA identificou explicitamente que NÃO pertence a Inajá, descarta
                if ia.get("pertence_a_inaja") is False:
                    logger.info(
                        "IA detectou publicação pertencente a outro município. Descartando."
                    )
                    stats["descartes_ia"] += 1
                    stats["descartes_vizinho"] += 1
                    refinadas[i] = None
                    continue

                if ia.get("tem_mencao_inaja") is False:
                    stats["descartes_ia"] += 1
                    refinadas[i] = None
                    continue

                # Segurança adicional: validar o campo "orgao" extraído
                orgao_ia = ia.get("orgao")
                if orgao_ia:
                    orgao_clean = _sem_acentos(orgao_ia).casefold()
                    palavras_orgao = [
                        "municipio de",
                        "prefeitura de",
                        "prefeitura municipal de",
                        "camara de",
                        "camara municipal de",
                    ]

                    if "inaja" not in orgao_clean:
                        if any(m in orgao_clean for m in MUNICIPIOS_VIZINHOS) or any(
                            p in orgao_clean for p in palavras_orgao
                        ):
                            logger.info(
                                "Filtro programático descartou órgão de outro município: %s",
                                orgao_ia,
                            )
                            stats["descartes_ia"] += 1
                            stats["descartes_vizinho"] += 1
                            refinadas[i] = None
                            continue

                # Pós-processamento: normalizar campos extraídos pela IA
                pub["texto_corrigido"] = ia.get("texto_corrigido") or pub.get("trecho")
                pub["resumo_ia"] = ia.get("resumo")
                pub["categoria_ia"] = ia.get("categoria") or pub.get("categoria")
                pub["orgao"] = _normalizar_orgao_ia(ia.get("orgao")) or pub.get("orgao")
                pub["tipo"] = normalizar_tipo_ato(
                    ia.get("tipo") or pub.get("tipo")
                )
                pub["numero"] = ia.get("numero") or pub.get("numero")
                pub["data_documento"] = _normalizar_data_ia(
                    ia.get("data_documento")
                ) or pub.get("data_documento")
                pub["assunto"] = ia.get("assunto") or pub.get("assunto")
                valor_extraido = _normalizar_valor_ia(ia.get("valor"))
                if valor_extraido:
                    valor_norm = (
                        _sem_acentos(valor_extraido)
                        .casefold()
                        .replace(" ", "")
                        .replace("r$", "")
                    )
                    trecho_norm = _sem_acentos(pub.get("trecho", "")).casefold()
                    resumo_norm = _sem_acentos(ia.get("resumo", "") or "").casefold()
                    no_texto = (
                        valor_norm in trecho_norm.replace(" ", "")
                        or valor_norm in resumo_norm.replace(" ", "")
                    )
                    if not no_texto:
                        match_resumo = re.search(
                            r"R\$\s*[\d.,]+", ia.get("resumo", "") or ""
                        )
                        if match_resumo:
                            valor_extraido = _normalizar_valor_ia(match_resumo.group(0))
                            logger.info(
                                "Valor corrigido via sanity check: %r -> %r",
                                ia.get("valor"),
                                valor_extraido,
                            )
                pub["valor"] = valor_extraido or pub.get("valor")
                tags = ia.get("tags")
                if isinstance(tags, list) and tags:
                    pub["tags"] = ", ".join(str(t) for t in tags[:3])
            refinadas[i] = pub

    resultado_final = [r for r in refinadas if r is not None]
    logger.info(
        "IA refinou %s de %s publicações (mantidas: %s, descartes_ia=%s)",
        sum(1 for r in resultado_final if r.get("resumo_ia")),
        len(publicacoes),
        len(resultado_final),
        stats["descartes_ia"],
    )
    return resultado_final, stats


def retry_pending_ia() -> int:
    """Tenta refinar com IA as publicações que ainda não foram processadas.

    Publicações com ``ia_processado=0`` (ou ``resumo_ia`` nulo) são buscadas no
    banco, reagrupadas pela edição de origem e enviadas novamente à API.
    Retorna o número de publicações atualizadas com sucesso (com resumo_ia).
    """
    if not ia_disponivel():
        logger.debug(
            "retry_pending_ia: IA indisponível (key/auth/refine) — pulando."
        )
        return 0

    pendentes = db.get_publicacoes_sem_ia()
    if not pendentes:
        return 0

    logger.info(
        "retry_pending_ia: %d publicação(ões) pendente(s) de refinamento IA.",
        len(pendentes),
    )

    from collections import defaultdict

    por_edicao: dict[int, list[dict]] = defaultdict(list)
    for pub in pendentes:
        por_edicao[pub["edicao_id"]].append(dict(pub))

    total_atualizadas = 0
    for edicao_id, pubs in por_edicao.items():
        if _auth_bloqueada:
            logger.warning(
                "retry_pending_ia: interrompido (auth inválida) após %s atualização(ões).",
                total_atualizadas,
            )
            break
        try:
            ids_antes = {
                int(p["id"]) for p in pubs if p.get("id") is not None
            }
            refinadas, _stats = refinar_publicacoes(pubs)
            ids_mantidas = {
                int(p["id"]) for p in refinadas if p.get("id") is not None
            }
            for pub in refinadas:
                # Só persiste se a IA realmente gerou resumo (evita falso "N atualizadas")
                if pub.get("resumo_ia"):
                    db.update_publicacao_ia(pub)
                    total_atualizadas += 1
            # Removidas da lista = descartadas pela IA (vizinho / sem menção)
            descartadas = ids_antes - ids_mantidas
            if descartadas:
                with db.connect() as conn:
                    for pid in descartadas:
                        conn.execute(
                            "DELETE FROM publicacoes WHERE id = ?", (pid,)
                        )
                logger.info(
                    "retry_pending_ia: removidas %s pub(s) descartadas edicao_id=%s",
                    len(descartadas),
                    edicao_id,
                )
        except Exception:
            logger.exception(
                "retry_pending_ia: falha ao refinar edicao_id=%s", edicao_id
            )
            continue

    logger.info(
        "retry_pending_ia: %d publicação(ões) com resumo_ia atualizado.",
        total_atualizadas,
    )
    return total_atualizadas

