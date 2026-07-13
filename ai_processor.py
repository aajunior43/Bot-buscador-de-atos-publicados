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
# Contador de calls no processo (ciclo / web)
_calls_no_ciclo: int = 0


def reset_ai_call_counter() -> None:
    global _calls_no_ciclo
    _calls_no_ciclo = 0


def ai_calls_no_ciclo() -> int:
    return _calls_no_ciclo


def _pode_chamar_ia() -> bool:
    lim = int(getattr(SETTINGS, "ai_max_calls_por_ciclo", 50) or 50)
    if lim <= 0:
        return True
    if _calls_no_ciclo >= lim:
        logger.warning(
            "Limite AI_MAX_CALLS_POR_CICLO=%s atingido — novas calls de IA neste ciclo ignoradas.",
            lim,
        )
        return False
    return True


def _registrar_call_ia() -> None:
    global _calls_no_ciclo
    _calls_no_ciclo += 1


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
   - "numero": SOMENTE se o trecho tiver explicitamente "Nº"/"nº"/"N."/"n." (ou "Nº." OCR) + dígitos do ATO
     (ex: "Decreto Nº 042/2026", "Contrato nº 04/2026", "Portaria n. 865/2026").
     Se NÃO houver marcador de número de ato → numero=null. NUNCA invente, NUNCA use RG/CPF/processo/CNPJ/CEP/ano isolado.
   - "data_documento": busque datas no texto no formato "DD de MÊS de AAAA" ou "DD/MM/AAAA" e normalize para "DD/MM/AAAA".
     Só se a data aparecer no trecho; senão null.
   - "valor": busque "R$ X.XXX,XX" no trecho. Para Dispensa/Contrato/Homologação/Extrato, em geral há valor.
     Só null se realmente não houver. Não confunda com processo, CPF, CNPJ ou datas.
4. Nunca retorne valor corrupto ou alucinado: se o texto diz "R$ 19.876,00", NUNCA retorne "R$ 220" nem parte do valor.
   O valor deve ser o completo, lido da expressão "R$ ...". Se o OCR estiver ilegível, prefira null a inventar.
5. OCR CONTEXTUAL (texto_corrigido): corrija erros típicos sem inventar fatos —
   nomes Inajá/Inaja → Inajá; CEP 87670-000; CNPJs 75.771.400/0001-48 e 76.970.318/0001-67;
   LEINº→LEI Nº; valores R$ com milhar; "Prefeitura Municipal de Inajá".
   Nunca invente dígitos de valor, número de ato ou nomes de pessoas/empresas ausentes.
6. Responda APENAS com um objeto JSON válido. Nunca inclua blocos markdown ```json ... ```, comentários ou explicações extras antes ou depois do JSON.

Campos do JSON de retorno:
- texto_corrigido: string — texto com erros de OCR corrigidos (contextual Inajá)
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
- tags: array de strings — até 3 marcadores curtos (ex: ["licitação", "obra", "saúde"])
- temas: array de strings — do vocabulário: licitacao, contrato, obra, saude, educacao, rh, fiscal, assistencia, infraestrutura, meio_ambiente, cultura, transporte, outros
- partes: objeto — {{"contratada": null|"nome empresa", "beneficiarios": [], "cargo_nomeado": null|"cargo", "publico_afetado": "frase curta"}}
- checklist: objeto — {{"tem_numero": bool, "tem_data": bool, "tem_valor": bool, "tem_orgao": bool, "tem_fundamentacao": bool, "tem_objeto": bool, "faltando": ["..."]}}
- importancia: inteiro 1 a 5 — relevância para gestão/transparência de Inajá
  (1=rotina menor, 3=relevante, 5=crítico: valores altos, licitação, nomeação, LRF)
- importancia_motivo: string curta explicando a nota
- notificar: boolean — true se vale alerta imediato; false se pode só arquivar"""

def _prompt_usuario(trecho: str) -> str:
    return f"Texto OCR:\n\n{trecho}\n\nExtraia os dados no JSON conforme solicitado."


_TRIAGE_PROMPT = """Você classifica publicações do jornal O Regional (Norte do Paraná) quanto a Inajá-PR.

Cidades VIZINHAS (NÃO são Inajá): {vizinhos}.
CNPJs de Inajá: 75.771.400/0001-48 e 76.970.318/0001-67. CEP: 87670-000.

Responda APENAS JSON:
{{
  "acao": "manter" | "descartar" | "so_mencao",
  "pertence_a_inaja": true/false,
  "motivo": "frase curta"
}}

- manter: ato/publicação oficial do município de Inajá (prefeitura, câmara, conselhos, fundos).
- descartar: outro município, publicidade, ou sem relação com Inajá.
- so_mencao: só cita Inajá em matéria/contexto sem ser o ato do município.
"""


def _prompt_triagem(trecho: str) -> str:
    return f"Texto OCR (pode ter erros):\n\n{trecho[:2500]}\n\nClassifique no JSON."


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


def _chamar_ia_json(
    system: str,
    user: str,
    *,
    timeout: int,
    max_tokens: int | None = None,
    temperature: float = 0.1,
) -> dict[str, Any] | None:
    if _auth_bloqueada:
        return None
    if not _pode_chamar_ia():
        return None
    content = ""
    try:
        _registrar_call_ia()
        resp = requests.post(
            SETTINGS.opencode_api_url,
            headers=_headers(),
            json={
                "model": SETTINGS.opencode_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens or SETTINGS.ai_max_tokens,
                "temperature": temperature,
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
        if not content.strip():
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return _tentar_recuperar_json(content)
    except requests.RequestException as exc:
        logger.warning("Erro na requisição ao OpenCode Go: %s", exc)
        return None
    except Exception:
        logger.exception("Falha ao chamar IA")
        return None


def _triar_publicacao(trecho: str, timeout: int) -> dict[str, Any] | None:
    """Etapa 1: manter | descartar | so_mencao (barata)."""
    trecho_limpo = _limpar_trecho_ocr(trecho)
    return _chamar_ia_json(
        _TRIAGE_PROMPT.format(
            vizinhos=", ".join(m.title() for m in MUNICIPIOS_VIZINHOS)
        ),
        _prompt_triagem(trecho_limpo),
        timeout=min(timeout, 45),
        max_tokens=min(400, SETTINGS.ai_max_tokens),
        temperature=0.0,
    )


def _extrair_publicacao(trecho: str, timeout: int) -> dict[str, Any] | None:
    if _auth_bloqueada:
        return None
    trecho_limpo = _limpar_trecho_ocr(trecho)
    # Etapa 1 — triagem
    triagem = _triar_publicacao(trecho_limpo, timeout)
    if triagem:
        acao = str(triagem.get("acao") or "").strip().casefold()
        if acao in {"descartar", "discard", "rejeitar"}:
            return {
                "pertence_a_inaja": False,
                "tem_mencao_inaja": False,
                "_triagem": triagem,
            }
        if acao in {"so_mencao", "so-mencao", "mencao"}:
            return {
                "pertence_a_inaja": False,
                "tem_mencao_inaja": True,
                "categoria": "materia_jornalistica",
                "resumo": triagem.get("motivo") or "Apenas menção a Inajá",
                "_triagem": triagem,
            }
        # manter → segue extração completa
    if not _pode_chamar_ia():
        return None
    content = ""
    try:
        _registrar_call_ia()
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
                # Anti-alucinação: campos devem ancorar no trecho OCR (#18)
                if getattr(SETTINGS, "ai_anti_alucinacao", True):
                    try:
                        from inteligencia import validar_campos_ia

                        ia = validar_campos_ia(pub, ia)
                    except Exception:
                        logger.debug("validar_campos_ia falhou", exc_info=True)
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
                # Número: prioriza padrão explícito no trecho; IA só se ancorado com N°
                pub["numero"] = _resolver_numero_final(pub, ia)
                pub["data_documento"] = _normalizar_data_ia(
                    ia.get("data_documento")
                ) or pub.get("data_documento")
                # Data em português extenso no resumo/texto se ainda nula
                if not pub.get("data_documento"):
                    pub["data_documento"] = _normalizar_data_ia(
                        ia.get("resumo") or ""
                    ) or _normalizar_data_ia(pub.get("trecho") or "")
                pub["assunto"] = ia.get("assunto") or pub.get("assunto")
                # Importância / alerta (mesmo JSON — sem call extra)
                if getattr(SETTINGS, "ai_importancia", True):
                    try:
                        imp = int(ia.get("importancia") or 0)
                    except (TypeError, ValueError):
                        imp = 0
                    if 1 <= imp <= 5:
                        pub["importancia"] = imp
                    else:
                        pub["importancia"] = _heuristica_importancia(pub, ia)
                    pub["importancia_motivo"] = (
                        ia.get("importancia_motivo")
                        or pub.get("importancia_motivo")
                        or ""
                    )
                    if "notificar" in ia:
                        pub["notificar_ia"] = bool(ia.get("notificar"))
                    else:
                        pub["notificar_ia"] = pub["importancia"] >= int(
                            getattr(SETTINGS, "ai_importancia_min_notificar", 3) or 3
                        )
                pub["valor"] = _resolver_valor_final(pub, ia)
                tags = ia.get("tags")
                if isinstance(tags, list) and tags:
                    pub["tags"] = ", ".join(str(t) for t in tags[:3])

                # Pack B: temas, partes, checklist, validação (mesmo JSON)
                try:
                    from inteligencia import (
                        montar_checklist_local,
                        normalizar_lista_temas,
                    )

                    if getattr(SETTINGS, "ai_temas", True):
                        temas = normalizar_lista_temas(
                            ia.get("temas") or ia.get("tags") or pub.get("tags")
                        )
                        if temas:
                            pub["temas"] = ", ".join(temas)
                    if getattr(SETTINGS, "ai_partes", True):
                        partes = ia.get("partes")
                        if isinstance(partes, dict) and any(partes.values()):
                            pub["partes_ia"] = _sanitizar_partes(partes, pub)
                    # Checklist DEPOIS de valor/número finais
                    if getattr(SETTINGS, "ai_checklist", True):
                        pub["checklist_ia"] = montar_checklist_local(pub, ia)
                    if getattr(SETTINGS, "ai_anti_alucinacao", True):
                        val = ia.get("_validacao") or {
                            "ok": True,
                            "flags": [],
                        }
                        pub["validacao_ia"] = val
                except Exception:
                    logger.debug("pack B pós-refine falhou", exc_info=True)
            refinadas[i] = pub

    # Anomalia (#3) — heurística com histórico por tipo
    if getattr(SETTINGS, "ai_anomalia", True):
        try:
            from inteligencia import detectar_anomalia

            for pub in refinadas:
                if pub is None:
                    continue
                hist = db.historico_valores_por_tipo(pub.get("tipo"), limit=80)
                is_a, motivo = detectar_anomalia(pub, hist)
                if is_a:
                    pub["anomalia"] = 1
                    pub["anomalia_motivo"] = motivo
                    # anomalia eleva notificação
                    if pub.get("notificar_ia") is False:
                        pub["notificar_ia"] = True
        except Exception:
            logger.debug("detecção de anomalia falhou", exc_info=True)

    resultado_final = [r for r in refinadas if r is not None]
    # Funde fragmentos do mesmo aditivo/contrato na mesma página
    try:
        from detector import _deduplicar_publicacoes, _fundir_fragmentos_mesmo_ato
        from inteligencia import montar_checklist_local

        resultado_final = _fundir_fragmentos_mesmo_ato(resultado_final)
        resultado_final = _deduplicar_publicacoes(resultado_final)
        # Pós-fusão: revalida nº/valor e checklist
        for pub in resultado_final:
            pub["numero"] = _resolver_numero_final(pub, {})
            if not pub.get("valor"):
                pub["valor"] = _resolver_valor_final(pub, {})
            if getattr(SETTINGS, "ai_checklist", True):
                pub["checklist_ia"] = montar_checklist_local(pub, {})
    except Exception:
        logger.debug("fusão/dedup pós-IA falhou", exc_info=True)
    logger.info(
        "IA refinou %s de %s publicações (mantidas: %s, descartes_ia=%s)",
        sum(1 for r in resultado_final if r.get("resumo_ia")),
        len(publicacoes),
        len(resultado_final),
        stats["descartes_ia"],
    )
    return resultado_final, stats


def _resolver_numero_final(pub: dict, ia: dict | None = None) -> str | None:
    """Prioriza nº explícito no OCR; IA só se ancorado com N°."""
    ia = ia or {}
    trecho = pub.get("trecho") or ""
    resumo = ia.get("resumo") or pub.get("resumo_ia") or ""
    corrigido = ia.get("texto_corrigido") or pub.get("texto_corrigido") or ""
    try:
        from detector import (
            _extrair_numero_preferencial,
            _numero_confiavel,
        )

        # 1) Padrão no trecho/corrigido
        for blob in (trecho, corrigido, resumo):
            pref = _extrair_numero_preferencial(blob, pub.get("tipo") or ia.get("tipo"))
            if pref:
                return pref
        # 2) IA / detector prévio, só se confiável
        cand = ia.get("numero") or pub.get("numero")
        return _numero_confiavel(
            cand,
            trecho=trecho,
            resumo=resumo,
            texto_corrigido=corrigido,
        )
    except Exception:
        return pub.get("numero")


def _resolver_valor_final(pub: dict, ia: dict | None = None) -> str | None:
    """Valor da IA, resumo ou já existente — com âncora no texto."""
    ia = ia or {}
    valor_extraido = _normalizar_valor_ia(ia.get("valor") or pub.get("valor"))
    if valor_extraido:
        if _valor_ancorado(valor_extraido, pub, ia) or _valor_digitos_no_texto(
            valor_extraido, pub, ia
        ):
            return valor_extraido
    do_resumo = _valor_do_resumo(ia.get("resumo") or pub.get("resumo_ia"))
    if do_resumo:
        return do_resumo
    # Última chance: R$ no trecho bruto
    m = re.search(r"R\$\s*[\d.]+,\d{2}", pub.get("trecho") or "")
    if m:
        return _normalizar_valor_ia(m.group(0))
    return None


def _sanitizar_partes(partes: dict, pub: dict) -> dict:
    """Corrige OCR óbvio em nomes (Emas→Elias se Elias no trecho)."""
    out = dict(partes)
    blob = _sem_acentos(
        " ".join(
            str(x or "")
            for x in (pub.get("trecho"), pub.get("resumo_ia"), pub.get("texto_corrigido"))
        )
    ).casefold()
    contratada = str(out.get("contratada") or "")
    if contratada:
        c_norm = _sem_acentos(contratada).casefold()
        # "Lourdes Emas" → Elias se o trecho tem elias
        if "emas" in c_norm and "elias" in blob:
            out["contratada"] = re.sub(
                r"(?i)\bEmas\b", "Elias", contratada
            )
        if "fernande" in c_norm and "fernandes" in blob:
            out["contratada"] = re.sub(
                r"(?i)\bFernande\b", "Fernandes", out["contratada"]
            )
    return out


def _valor_do_resumo(resumo: str | None) -> str | None:
    if not resumo:
        return None
    m = re.search(r"R\$\s*[\d.]+,\d{2}", resumo)
    if not m:
        m = re.search(r"R\$\s*[\d.,]+", resumo)
    if not m:
        return None
    return _normalizar_valor_ia(m.group(0))


def _valor_ancorado(valor: str, pub: dict, ia: dict | None = None) -> bool:
    """True se o valor (ou dígitos) aparece no trecho/resumo."""
    ia = ia or {}
    valor_norm = (
        _sem_acentos(valor).casefold().replace(" ", "").replace("r$", "")
    )
    trecho_norm = _sem_acentos(pub.get("trecho", "")).casefold().replace(" ", "")
    resumo_norm = (
        _sem_acentos(ia.get("resumo") or pub.get("resumo_ia") or "")
        .casefold()
        .replace(" ", "")
    )
    if valor_norm and (
        valor_norm in trecho_norm or valor_norm in resumo_norm
    ):
        return True
    return _valor_digitos_no_texto(valor, pub, ia)


def _valor_digitos_candidatos(valor: str) -> list[str]:
    """Gera variantes de dígitos (com/sem centavos) para match em OCR sujo."""
    digs = re.sub(r"\D", "", valor or "")
    if len(digs) < 3:
        return []
    out = [digs]
    # R$ 255.800,00 → digs=25580000; OCR pode ter só 255800
    if len(digs) > 3 and digs.endswith("00"):
        out.append(digs[:-2])
    return out


def _valor_digitos_no_texto(valor: str, pub: dict, ia: dict | None = None) -> bool:
    """Aceita valor se a sequência de dígitos (sem formatação) está no OCR/resumo."""
    candidatos = _valor_digitos_candidatos(valor)
    if not candidatos:
        return False
    ia = ia or {}
    blobs = [
        re.sub(r"\D", "", pub.get("trecho") or ""),
        re.sub(r"\D", "", ia.get("resumo") or pub.get("resumo_ia") or ""),
        re.sub(r"\D", "", ia.get("texto_corrigido") or pub.get("texto_corrigido") or ""),
    ]
    for digs in candidatos:
        if any(digs in b for b in blobs if b):
            return True
    return False


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


def _heuristica_importancia(pub: dict, ia: dict | None = None) -> int:
    """Fallback se a IA não devolver importância válida."""
    texto = " ".join(
        str(x or "")
        for x in (
            pub.get("tipo"),
            pub.get("assunto"),
            pub.get("resumo_ia"),
            (ia or {}).get("resumo"),
            pub.get("valor"),
        )
    )
    n = _sem_acentos(texto).casefold()
    score = 2
    if any(k in n for k in ("contrato", "dispensa", "homolog", "licitacao", "pregao")):
        score = max(score, 4)
    if any(k in n for k in ("rgf", "rreo", "lrf", "balanco", "fiscal")):
        score = max(score, 4)
    if any(k in n for k in ("nomeacao", "exoneracao", "portaria", "decreto")):
        score = max(score, 3)
    if pub.get("valor") or (ia or {}).get("valor"):
        score = max(score, 3)
        digs = re.sub(r"\D", "", str(pub.get("valor") or (ia or {}).get("valor") or ""))
        if len(digs) >= 7:  # >= ~R$ 100.000
            score = max(score, 5)
    return min(5, max(1, score))


def gerar_explicacao_leiga(pub: dict) -> str | None:
    """Explica o ato em linguagem simples (3–5 frases)."""
    if not getattr(SETTINGS, "ai_explicacao", True) or not ia_disponivel():
        return None
    trecho = _limpar_trecho_ocr(
        pub.get("texto_corrigido") or pub.get("trecho") or ""
    )[:3000]
    if not trecho:
        return None
    system = (
        "Você explica publicações oficiais de Inajá-PR para cidadãos leigos. "
        "Use português claro, sem juridiquês desnecessário. "
        "NÃO invente números, valores ou órgãos que não estejam no texto. "
        "Se o trecho for confuso por OCR, diga o que dá para entender. "
        'Responda JSON: {"explicacao": "3 a 5 frases", "publico_afetado": "frase curta"}'
    )
    user = (
        f"Tipo: {pub.get('tipo')}\nNúmero: {pub.get('numero')}\n"
        f"Órgão: {pub.get('orgao')}\nValor: {pub.get('valor')}\n"
        f"Resumo: {pub.get('resumo_ia') or pub.get('assunto')}\n\n"
        f"Trecho OCR:\n{trecho}"
    )
    data = _chamar_ia_json(system, user, timeout=max(20, SETTINGS.ai_timeout_seconds), max_tokens=600)
    if not data:
        return None
    exp = (data.get("explicacao") or "").strip()
    afet = (data.get("publico_afetado") or "").strip()
    if afet:
        exp = f"{exp}\n\nQuem pode ser afetado: {afet}".strip()
    return exp or None


def gerar_resumo_periodo_ia(texto_base: str, *, dia: str) -> str | None:
    """Reescreve o resumo diário em tom de assessoria (1 call)."""
    if not getattr(SETTINGS, "ai_resumo_diario", True) or not ia_disponivel():
        return None
    if not (texto_base or "").strip():
        return None
    system = (
        "Você é assessor de comunicação da Prefeitura de Inajá-PR. "
        "Com base na lista de atos oficiais do dia, escreva um resumo executivo em português: "
        "1 parágrafo de abertura + até 5 bullets do que importa (valores, licitações, pessoal, LRF). "
        "Não invente atos que não estejam na lista. "
        'JSON: {"titulo": "...", "paragrafo": "...", "bullets": ["...", "..."]}'
    )
    user = f"Data: {dia}\n\nLista/base:\n{texto_base[:6000]}"
    data = _chamar_ia_json(
        system, user, timeout=max(30, SETTINGS.ai_timeout_seconds), max_tokens=900
    )
    if not data:
        return None
    linhas = []
    if data.get("titulo"):
        linhas.append(str(data["titulo"]).strip())
        linhas.append("")
    if data.get("paragrafo"):
        linhas.append(str(data["paragrafo"]).strip())
        linhas.append("")
    bullets = data.get("bullets") or []
    if isinstance(bullets, list):
        for b in bullets[:6]:
            linhas.append(f"• {str(b).strip()}")
    texto = "\n".join(linhas).strip()
    return texto or None


def auditar_so_mencao(trechos: list[dict], *, titulo: str = "") -> dict | None:
    """Classifica por que há menção a Inajá sem publicação estruturada (#17 FN)."""
    if not (
        getattr(SETTINGS, "ai_auditoria_so_mencao", True)
        or getattr(SETTINGS, "ai_fn_recuperacao", True)
    ) or not ia_disponivel():
        return None
    if not trechos:
        return {
            "classificacao": "sem_trecho",
            "motivo": "Nenhuma menção bruta gravada",
            "acao_sugerida": "revisar_ocr",
            "paginas_sugeridas": [],
        }
    blocos = []
    for t in trechos[:8]:
        blocos.append(
            f"pág.{t.get('pagina')}: {(t.get('trecho') or '')[:400]}"
        )
    system = (
        "Você audita detecções do monitor de atos de Inajá-PR no jornal O Regional. "
        "Há menção a Inajá mas o detector não extraiu publicação oficial estruturada. "
        "Classifique e, se parecer ato perdido (falso negativo), indique páginas a reprocessar. "
        'JSON: {"classificacao": "ato_perdido"|"materia"|"outro_municipio"|"ruido_ocr"|"outro", '
        '"motivo": "frase", "acao_sugerida": "reprocessar"|"ignorar"|"revisar_manual", '
        '"confianca": 0.0-1.0, "paginas_sugeridas": [int], '
        '"trechos_prioritarios": ["resumo curto do trecho prioritário"]}'
    )
    user = f"Edição: {titulo}\n\nTrechos:\n" + "\n---\n".join(blocos)
    data = _chamar_ia_json(
        system, user, timeout=max(25, SETTINGS.ai_timeout_seconds), max_tokens=500
    )
    if not data:
        return None
    data["classificacao"] = str(data.get("classificacao") or "outro").casefold()
    pags = data.get("paginas_sugeridas") or []
    if not isinstance(pags, list):
        pags = []
    clean_pags = []
    for p in pags:
        try:
            clean_pags.append(int(p))
        except (TypeError, ValueError):
            continue
    if not clean_pags:
        # fallback: páginas dos trechos
        for t in trechos[:5]:
            try:
                clean_pags.append(int(t.get("pagina")))
            except (TypeError, ValueError):
                pass
    data["paginas_sugeridas"] = clean_pags[:8]
    return data


def triar_ruidos_lote(trechos: list[dict], *, titulo: str = "") -> list[dict] | None:
    """#5 — classifica até 15 menções em 1 call."""
    if not getattr(SETTINGS, "ai_triagem_lote", True) or not ia_disponivel():
        return None
    if not trechos:
        return []
    itens = trechos[:15]
    blocos = []
    for i, t in enumerate(itens, start=1):
        blocos.append(
            f"[{i}] pág={t.get('pagina')} termo={t.get('termo_encontrado') or t.get('termo')}\n"
            f"{(t.get('trecho') or '')[:350]}"
        )
    system = (
        "Classifique cada trecho do jornal O Regional (foco Inajá-PR). "
        "classificacao: ato | materia | vizinho | ruido_ocr | outro. "
        'JSON: {"itens": [{"i": 1, "classificacao": "...", "confianca": 0.0-1.0, "motivo": "frase curta"}]}'
    )
    user = f"Edição: {titulo}\n\n" + "\n---\n".join(blocos)
    data = _chamar_ia_json(
        system, user, timeout=max(40, SETTINGS.ai_timeout_seconds), max_tokens=900
    )
    if not data:
        return None
    raw = data.get("itens") or data.get("resultados") or []
    if not isinstance(raw, list):
        return None
    out: list[dict] = []
    by_i = {}
    for r in raw:
        try:
            by_i[int(r.get("i"))] = r
        except (TypeError, ValueError):
            continue
    for i, t in enumerate(itens, start=1):
        r = by_i.get(i) or {}
        out.append(
            {
                "pagina": t.get("pagina"),
                "trecho": (t.get("trecho") or "")[:200],
                "classificacao": str(r.get("classificacao") or "outro").casefold(),
                "confianca": r.get("confianca"),
                "motivo": r.get("motivo") or "",
            }
        )
    return out


def narrar_linha_tempo(pubs: list[dict]) -> str | None:
    """#7 — narra cadeia de atos relacionados."""
    if not getattr(SETTINGS, "ai_timeline", True) or not ia_disponivel():
        return None
    if not pubs:
        return None
    linhas = []
    for i, p in enumerate(pubs[:12], start=1):
        linhas.append(
            f"[{i}] {p.get('data_publicacao') or p.get('data_documento') or '?'} | "
            f"{p.get('tipo')} {p.get('numero') or ''} | {p.get('orgao') or ''} | "
            f"{p.get('valor') or ''} | {(p.get('resumo_ia') or p.get('assunto') or '')[:120]}"
        )
    system = (
        "Você narra a linha do tempo de atos oficiais de Inajá-PR (contratos/aditivos/homologações). "
        "Use só os itens listados. Seja objetivo em português. "
        'JSON: {"narrativa": "3-8 frases ou bullets", "elo": "como se conectam"}'
    )
    user = "Atos em ordem:\n" + "\n".join(linhas)
    data = _chamar_ia_json(
        system, user, timeout=max(35, SETTINGS.ai_timeout_seconds), max_tokens=700
    )
    if not data:
        return None
    narr = (data.get("narrativa") or "").strip()
    elo = (data.get("elo") or "").strip()
    if elo:
        narr = f"{narr}\n\nElo: {elo}".strip()
    return narr or None


def comparar_com_similares(alvo: dict, similares: list[dict]) -> dict[str, Any] | None:
    """#8 — compara ato com candidatos semelhantes."""
    if not getattr(SETTINGS, "ai_similares", True) or not ia_disponivel():
        return None
    if not similares:
        return {
            "resumo": "Não há atos similares no acervo para comparar.",
            "diferencas": [],
            "citacoes": [],
        }
    def _fmt(p: dict, i: int) -> str:
        return (
            f"[{i}] id={p.get('id')} {p.get('tipo')} {p.get('numero') or ''} "
            f"{p.get('orgao') or ''} valor={p.get('valor') or '—'} "
            f"data={p.get('data_publicacao') or p.get('data_documento') or '?'}\n"
            f"{(p.get('resumo_ia') or p.get('assunto') or '')[:180]}"
        )

    system = (
        "Compare o ato ALVO com atos SIMILARES de Inajá-PR. Não invente. "
        'JSON: {"resumo": "2-4 frases", "diferencas": ["..."], "citacoes": [1,2]}'
    )
    user = (
        "ALVO:\n"
        + _fmt(alvo, 0)
        + "\n\nSIMILARES:\n"
        + "\n".join(_fmt(s, i) for i, s in enumerate(similares[:6], start=1))
    )
    data = _chamar_ia_json(
        system, user, timeout=max(40, SETTINGS.ai_timeout_seconds), max_tokens=700
    )
    if not data:
        return None
    cits = data.get("citacoes") or []
    if not isinstance(cits, list):
        cits = []
    citacoes = []
    for idx in cits:
        try:
            i = int(idx) - 1
            if 0 <= i < len(similares):
                citacoes.append(
                    {
                        "id": similares[i].get("id"),
                        "edicao_id": similares[i].get("edicao_id"),
                        "tipo": similares[i].get("tipo"),
                        "numero": similares[i].get("numero"),
                    }
                )
        except (TypeError, ValueError):
            continue
    diffs = data.get("diferencas") or []
    if not isinstance(diffs, list):
        diffs = [str(diffs)]
    return {
        "resumo": str(data.get("resumo") or "").strip(),
        "diferencas": [str(d) for d in diffs[:8]],
        "citacoes": citacoes,
    }


def responder_pergunta_atos(
    pergunta: str, contextos: list[dict]
) -> dict[str, Any] | None:
    """Responde pergunta com base nos trechos/resumos fornecidos (RAG simples)."""
    if not getattr(SETTINGS, "ai_chat", True) or not ia_disponivel():
        return None
    if not (pergunta or "").strip():
        return None
    if not contextos:
        return {
            "resposta": "Não encontrei atos no acervo que batam com essa pergunta.",
            "citacoes": [],
        }
    blocos = []
    for i, c in enumerate(contextos[:8], start=1):
        blocos.append(
            f"[{i}] id={c.get('id')} edicao_id={c.get('edicao_id')} "
            f"tipo={c.get('tipo')} num={c.get('numero')} orgao={c.get('orgao')} "
            f"data={c.get('data_publicacao')} valor={c.get('valor')}\n"
            f"resumo={c.get('resumo_ia') or c.get('assunto') or ''}\n"
            f"trecho={(c.get('trecho') or '')[:500]}"
        )
    system = (
        "Você responde perguntas sobre atos oficiais de Inajá-PR com base APENAS nos "
        "trechos numerados fornecidos. Cite os números [1], [2] usados. "
        "Se a resposta não estiver nos trechos, diga que não sabe. Não invente. "
        'JSON: {"resposta": "...", "citacoes": [1, 2]}'
    )
    user = f"Pergunta: {pergunta}\n\nFontes:\n" + "\n\n".join(blocos)
    data = _chamar_ia_json(
        system, user, timeout=max(40, SETTINGS.ai_timeout_seconds), max_tokens=800
    )
    if not data:
        return None
    cits = data.get("citacoes") or []
    if not isinstance(cits, list):
        cits = []
    # Mapear índices → ids
    citacoes = []
    for idx in cits:
        try:
            i = int(idx) - 1
            if 0 <= i < len(contextos):
                citacoes.append(
                    {
                        "id": contextos[i].get("id"),
                        "edicao_id": contextos[i].get("edicao_id"),
                        "tipo": contextos[i].get("tipo"),
                        "numero": contextos[i].get("numero"),
                        "orgao": contextos[i].get("orgao"),
                    }
                )
        except (TypeError, ValueError):
            continue
    return {
        "resposta": str(data.get("resposta") or "").strip(),
        "citacoes": citacoes,
    }

