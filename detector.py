from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from config import SETTINGS
from ocr_processor import PageText, TextBlock
from ai_processor import refinar_publicacoes


BASE_TERMS = [
    "Inajá",
    "Inaja",
    "Prefeitura de Inajá",
    "Prefeitura Municipal de Inajá",
    "Câmara de Inajá",
    "Câmara Municipal de Inajá",
    "Município de Inajá",
    "Municipio de Inaja",
    "75.771.400/0001-48",
]

GENERIC_TERMS = {"inajá", "inaja"}
CEP_RE = re.compile(r"\b87\d{3}-\d{3}\b")
CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
VALOR_RE = re.compile(r"R\s*[$S]\s*[\d\.\,]+", re.IGNORECASE)
VALOR_DE_RE = re.compile(
    r"valor\s+de\s+(R\s*[$S]\s*[\d\.\,]+)", re.IGNORECASE
)
DATA_EXTENSO_RE = re.compile(
    r"\b\d{1,2}\s+de\s+[A-Za-zçÇãÃéÉ]+\s+de\s+\d{4}\b",
    re.IGNORECASE,
)
DATA_NUMERICA_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
TIPOS_ATO = [
    "DECRETO",
    "PORTARIA",
    "LEI",
    "EDITAL",
    "AVISO",
    "EXTRATO",
    "DISPENSA",
    "INEXIGIBILIDADE",
    "HOMOLOGAÇÃO",
    "ADJUDICAÇÃO",
    "TERMO",
    "RESOLUÇÃO",
]
TIPO_ATO_RE = re.compile(
    r"\b("
    + "|".join(re.escape(tipo) for tipo in TIPOS_ATO)
    + r")\b\s*(?:N[º°O.]?\s*)?(\d{1,6}(?:[./-]\d{1,4})?)?",
    re.IGNORECASE,
)
LINHA_TITULO_ATO_RE = re.compile(
    r"^\s*(?:"
    r"(?:"
    + "|".join(re.escape(tipo) for tipo in TIPOS_ATO)
    + r")\b"
    r"|(?:[A-ZÁÉÍÓÚÃÕÇ ]{3,}\s+-\s+)?(?:"
    + "|".join(re.escape(tipo) for tipo in TIPOS_ATO)
    + r")\b"
    r")",
    re.IGNORECASE,
)
LINHA_ASSUNTO_RE = re.compile(
    r"\b(S[ÚU]MULA|EMENTA|OBJETO|ASSUNTO)\b\s*[:\-]?\s*(.+)",
    re.IGNORECASE,
)
REFERENCIA_LEGAL_RE = re.compile(
    r"\b(lei\s+(federal|estadual|org[aâ]nica)|art\.?\s*\d+|inciso|par[aá]grafo|loa)\b",
    re.IGNORECASE,
)
SINAIS_OFICIAIS = [
    "prefeitura municipal",
    "camara municipal",
    "municipio de",
    "estado do parana",
    "cnpj",
    "lei organica",
    "licitacao",
    "processo",
    "decreto",
    "portaria",
    "edital",
]


@dataclass(frozen=True)
class DetectionResult:
    encontrado: bool
    edicao_id: int
    edicao_titulo: str
    paginas_com_mencao: list[int]
    trechos: list[dict]
    termos_encontrados: list[str]
    mencoes_db: list[dict]
    publicacoes: list[dict]


def _sem_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in normalizado if not unicodedata.combining(ch))


def _snippet(texto: str, inicio: int, fim: int, margem: int = 120) -> str:
    ini = max(0, inicio - margem)
    end = min(len(texto), fim + margem)
    trecho = " ".join(texto[ini:end].split())
    return f"...{trecho}..."


def _limpar(texto: str) -> str:
    return " ".join((texto or "").split())


def _normalizar_ocr_para_extracao(texto: str) -> str:
    normalizado = texto or ""
    normalizado = re.sub(r"\bLEI\s*N[º°O.]", "LEI Nº", normalizado, flags=re.IGNORECASE)
    normalizado = re.sub(r"\bLEIN[º°O.]", "LEI Nº", normalizado, flags=re.IGNORECASE)
    normalizado = re.sub(r"\bPORTARIAN[º°O.]", "PORTARIA Nº", normalizado, flags=re.IGNORECASE)
    normalizado = re.sub(r"\bDECRETON[º°O.]", "DECRETO Nº", normalizado, flags=re.IGNORECASE)
    return normalizado


def _contexto(texto: str, inicio: int, fim: int, margem: int = 180) -> str:
    ini = max(0, inicio - margem)
    end = min(len(texto), fim + margem)
    return texto[ini:end]


def _termos() -> list[str]:
    termos = BASE_TERMS + SETTINGS.extra_terms
    vistos: set[str] = set()
    resultado: list[str] = []
    for termo in termos:
        chave = _sem_acentos(termo).casefold()
        if termo and chave not in vistos:
            vistos.add(chave)
            resultado.append(termo)
    return resultado


def _cep_de_inaja(cep: str) -> bool:
    numeros = cep.replace("-", "")
    prefixos = SETTINGS.inaja_cep_prefixes or ["87670"]
    return any(numeros.startswith(prefixo.replace("-", "")) for prefixo in prefixos)


def _contexto_ignorado_para_mencao_generica(
    texto: str,
    inicio: int,
    fim: int,
) -> bool:
    contexto_norm = _sem_acentos(_contexto(texto, inicio, fim)).casefold()
    termos = [
        _sem_acentos(termo).casefold()
        for termo in SETTINGS.ignore_context_terms
        if termo.strip()
    ]
    encontrados = [termo for termo in termos if termo in contexto_norm]
    return "distribuicao avulsa" in contexto_norm or len(encontrados) >= 2


def _segmentos(pagina: PageText) -> list[TextBlock]:
    if pagina.blocks:
        return _agrupar_blocos_oficiais(
            [block for block in pagina.blocks if block.texto.strip()]
        )
    return [TextBlock(pagina=pagina.pagina, bloco=1, texto=pagina.texto)]


def _coluna_do_bloco(bloco: TextBlock) -> int:
    return bloco.bloco // 1000 if bloco.bloco >= 1000 else 0


def _bbox_top(bloco: TextBlock) -> int:
    return bloco.bbox[1] if bloco.bbox else 0


def _bbox_bottom(bloco: TextBlock) -> int:
    return bloco.bbox[3] if bloco.bbox else _bbox_top(bloco)


def _mesclar_blocos(blocos: list[TextBlock]) -> TextBlock:
    primeiro = blocos[0]
    texto = "\n".join(bloco.texto for bloco in blocos if bloco.texto.strip())
    bboxes = [bloco.bbox for bloco in blocos if bloco.bbox]
    bbox = None
    if bboxes:
        bbox = (
            min(item[0] for item in bboxes),
            min(item[1] for item in bboxes),
            max(item[2] for item in bboxes),
            max(item[3] for item in bboxes),
        )
    return TextBlock(
        pagina=primeiro.pagina,
        bloco=primeiro.bloco,
        texto=texto,
        bbox=bbox,
    )


def _inicia_publicacao_inaja(texto: str) -> bool:
    return _extrair_orgao(texto) is not None or _linha_inicia_ato(texto)


def _linha_inicia_ato(texto: str) -> bool:
    for linha in texto.splitlines():
        linha_limpa = _limpar(linha)
        if not linha_limpa:
            continue
        if REFERENCIA_LEGAL_RE.search(linha_limpa) and not re.search(
            r"\b(LEI|DECRETO|PORTARIA|EDITAL|AVISO|EXTRATO|TERMO|RESOLUÇÃO)\b\s*N[º°O.]?",
            linha_limpa,
            re.IGNORECASE,
        ):
            return False
        return bool(LINHA_TITULO_ATO_RE.search(linha_limpa))
    return False


def _agrupar_blocos_oficiais(blocos: list[TextBlock]) -> list[TextBlock]:
    if not blocos:
        return []

    agrupados: list[TextBlock] = []
    por_coluna: dict[int, list[TextBlock]] = {}
    for bloco in blocos:
        por_coluna.setdefault(_coluna_do_bloco(bloco), []).append(bloco)

    for _coluna, itens in sorted(por_coluna.items()):
        atual: list[TextBlock] = []

        def flush() -> None:
            nonlocal atual
            if atual:
                agrupados.append(_mesclar_blocos(atual) if len(atual) > 1 else atual[0])
                atual = []

        for bloco in sorted(itens, key=lambda item: (_bbox_top(item), item.bloco)):
            inicia_inaja = _inicia_publicacao_inaja(bloco.texto)
            if inicia_inaja:
                flush()
                atual = [bloco]
                continue

            if atual:
                gap = _bbox_top(bloco) - _bbox_bottom(atual[-1])
                if gap <= 420:
                    atual.append(bloco)
                    continue
                flush()

            agrupados.append(bloco)
        flush()

    return sorted(
        agrupados,
        key=lambda bloco: (
            _coluna_do_bloco(bloco),
            _bbox_top(bloco),
            bloco.bloco,
        ),
    )


def _categoria(texto: str) -> str:
    texto_norm = _sem_acentos(texto).casefold()
    if _contexto_ignorado_para_mencao_generica(texto, 0, min(len(texto), 1)):
        return "patrocinador_distribuicao"
    if any(sinal in texto_norm for sinal in SINAIS_OFICIAIS):
        return "publicacao_oficial"
    return "materia_jornalistica"


def _extrair_orgao(texto: str) -> str | None:
    inicio_norm = _sem_acentos(_limpar(texto)[:220]).casefold()
    if re.match(r"^\s*(art\.?\s*\d+|paragrafo|inciso)\b", inicio_norm):
        return None
    if re.match(
        r"^(prefeitura( municipal)?( do municipio)? de inaja|prefeitura municipal de inaja)\b",
        inicio_norm,
    ):
        return "Prefeitura Municipal de Inajá"
    if re.match(r"^(camara( municipal)? de inaja|camara municipal de inaja)\b", inicio_norm):
        return "Câmara Municipal de Inajá"
    if re.match(r"^municipio de inaja\b", inicio_norm):
        return "Município de Inajá"
    if "75.771.400/0001-48" in texto or "76.970.318/0001" in texto:
        return "Prefeitura Municipal de Inajá"
    return None


def _extrair_tipo_numero(texto: str) -> tuple[str | None, str | None]:
    texto = _normalizar_ocr_para_extracao(texto)
    linhas = [_limpar(linha) for linha in texto.splitlines() if _limpar(linha)]
    for linha in linhas:
        tem_marcador_explicito = re.search(
            r"\b("
            + "|".join(re.escape(tipo) for tipo in TIPOS_ATO)
            + r")\b\s*N[º°O.]?",
            linha,
            re.IGNORECASE,
        )
        if (
            REFERENCIA_LEGAL_RE.search(linha)
            and not LINHA_TITULO_ATO_RE.search(linha)
            and not tem_marcador_explicito
        ):
            continue
        if not LINHA_TITULO_ATO_RE.search(linha) and not tem_marcador_explicito:
            continue
        match = TIPO_ATO_RE.search(linha)
        if not match:
            continue
        tipo = match.group(1)
        numero = match.group(2)
        if numero and _numero_parece_documento_pessoal(numero, linha):
            numero = None
        if _sem_acentos(tipo).casefold() == "lei" and REFERENCIA_LEGAL_RE.search(linha):
            continue
        if _sem_acentos(tipo).casefold() == "lei" and not re.search(r"N[º°O.]?", linha, re.IGNORECASE):
            continue
        if REFERENCIA_LEGAL_RE.search(linha) and not re.search(r"N[º°O.]?", linha, re.IGNORECASE):
            continue
        return tipo.title(), _normalizar_numero_ato(numero) if numero else None
    return None, None


def _numero_parece_documento_pessoal(numero: str, linha: str) -> bool:
    if CPF_RE.search(linha):
        return True
    numero_limpo = numero.strip()
    if re.search(r"N[º°O.]?\s*" + re.escape(numero_limpo), linha, re.IGNORECASE):
        return False
    if "." in numero_limpo and "/" not in numero_limpo:
        return True
    contexto = _sem_acentos(linha).casefold()
    return any(chave in contexto for chave in ("cpf", "rg", "cnpj"))


def _normalizar_numero_ato(numero: str) -> str:
    valor = numero.strip().strip(".,;:")
    if "/" in valor or "-" in valor or "." in valor:
        return valor
    if len(valor) in {6, 7, 8}:
        return f"{valor[:-4]}/{valor[-4:]}"
    return valor


def _extrair_data(texto: str) -> str | None:
    texto = _normalizar_ocr_para_extracao(texto)
    match = DATA_EXTENSO_RE.search(texto)
    if match:
        return _limpar(match.group(0))
    match = DATA_NUMERICA_RE.search(texto)
    if match:
        return _limpar(match.group(0))
    return None


def _extrair_assunto(texto: str) -> str | None:
    texto = _normalizar_ocr_para_extracao(texto)
    linhas = [_limpar(linha) for linha in texto.splitlines() if _limpar(linha)]
    for idx, linha in enumerate(linhas):
        if not LINHA_TITULO_ATO_RE.search(linha):
            continue
        match_mesma_linha = LINHA_ASSUNTO_RE.search(linha)
        if match_mesma_linha and match_mesma_linha.group(2).strip():
            return _limpar(
                f"{match_mesma_linha.group(1).title()}: {match_mesma_linha.group(2).strip()}"
            )[:300]
        proximas = linhas[idx + 1 : idx + 5]
        for candidata in proximas:
            match = LINHA_ASSUNTO_RE.search(candidata)
            if match and match.group(2).strip():
                return _limpar(f"{linha} - {match.group(2).strip()}")[:300]
        for candidata in proximas:
            if _parece_linha_de_rodape_ou_assinatura(candidata):
                continue
            if LINHA_TITULO_ATO_RE.search(candidata):
                continue
            return _limpar(candidata)[:300]
        return _limpar(linha)[:300]

    for linha in linhas:
        match = LINHA_ASSUNTO_RE.search(linha)
        if match and match.group(2).strip():
            return _limpar(f"{match.group(1).title()}: {match.group(2).strip()}")[:300]

    palavras_chave = [
        "abertura",
        "credito",
        "crédito",
        "licitacao",
        "licitação",
        "homologar",
        "adjudicar",
        "objeto",
        "contratação",
        "contratacao",
    ]
    for linha in linhas:
        linha_norm = _sem_acentos(linha).casefold()
        if any(_sem_acentos(palavra).casefold() in linha_norm for palavra in palavras_chave):
            return _limpar(linha)[:300]
    for linha in linhas:
        if _parece_linha_de_rodape_ou_assinatura(linha):
            continue
        return _limpar(linha)[:300]
    return None


def _parece_linha_de_rodape_ou_assinatura(linha: str) -> bool:
    inicio = _sem_acentos(_limpar(linha)[:160]).casefold()
    return bool(
        re.match(
            r"^(cep|e-mail|email|fone|telefone|gabinete do prefeito|joao eder|assinado|prefeito municipal)\b",
            inicio,
        )
    )


def _parece_fragmento_continuacao(texto: str, tipo: str | None, orgao: str | None) -> bool:
    inicio = _sem_acentos(_limpar(texto)[:140]).casefold()
    if tipo or orgao:
        return False
    return bool(
        re.match(r"^(art\.?\s*\d+|paragrafo|inciso|gabinete do prefeito|joao eder|assinado)", inicio)
    )


def _tem_cabecalho_ato(texto: str) -> bool:
    return any(
        LINHA_TITULO_ATO_RE.search(_limpar(linha)) or LINHA_ASSUNTO_RE.search(_limpar(linha))
        for linha in texto.splitlines()
        if _limpar(linha)
    )


def _corrigir_ocr_basico(texto: str) -> str:
    substituicoes = {
        "www inaja.pr gov br": "www.inaja.pr.gov.br",
        "TPCA/IBGE": "IPCA/IBGE",
        "Art,": "Art.",
        "virgnta": "vírgula",
        "deforma ligital": "de forma digital",
        "Agsirado": "Assinado",
        "Municipio de Inajá": "Município de Inajá",
        "Prefeito Mui ral": "Prefeito Municipal",
        "servidores públicas": "servidores públicos",
        "Município do Inajá": "Município de Inajá",
        "LEINº": "LEI Nº",
    }
    corrigido = texto
    for errado, certo in substituicoes.items():
        corrigido = corrigido.replace(errado, certo)
    corrigido = re.sub(r"prefeitura[gq@d]inaja\.?\s*pr\.?\s*gov\.?\s*br", "prefeitura@inaja.pr.gov.br", corrigido, flags=re.IGNORECASE)
    corrigido = re.sub(r"prefeituraminaia\.?\s*pr\.?\s*gov\.?\s*br", "prefeitura@inaja.pr.gov.br", corrigido, flags=re.IGNORECASE)
    return _limpar(corrigido)


def _resumir_assunto(texto: str | None) -> str | None:
    if not texto:
        return None
    assunto = _corrigir_ocr_basico(texto)
    assunto = re.split(
        r"\b(A CÂMARA MUNICIPAL|A CAMARA MUNICIPAL|Art\.?\s*\d+|RESOLVE)\b",
        assunto,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return assunto.strip(" .,-")[:300] or None


def _extrair_valor(texto: str) -> str | None:
    match = VALOR_DE_RE.search(texto)
    if match:
        return _normalizar_valor(match.group(1))

    valores = [_normalizar_valor(match.group(0)) for match in VALOR_RE.finditer(texto)]
    if not valores:
        return None
    return max(valores, key=_valor_float)


def _normalizar_valor(valor: str) -> str:
    normalizado = re.sub(r"^R\s*[$S]", "R$", valor.strip(), flags=re.IGNORECASE)
    return normalizado.strip(".,;:")


def _valor_float(valor: str) -> float:
    numeros = re.sub(r"[^\d,\.]", "", valor)
    if "," in numeros:
        numeros = numeros.replace(".", "").replace(",", ".")
    try:
        return float(numeros)
    except ValueError:
        return 0.0


def _publicacao_do_segmento(segmento: TextBlock, termos: set[str]) -> dict | None:
    categoria = _categoria(segmento.texto)
    if categoria == "patrocinador_distribuicao":
        return None

    orgao = _extrair_orgao(segmento.texto)
    tipo, numero = _extrair_tipo_numero(segmento.texto)
    tem_cabecalho = _tem_cabecalho_ato(segmento.texto)
    if not (orgao or termos):
        return None
    if _parece_fragmento_continuacao(segmento.texto, tipo, orgao):
        return None
    if categoria == "publicacao_oficial" and not (orgao or tipo):
        return None
    if orgao and not tipo and not LINHA_ASSUNTO_RE.search(segmento.texto):
        assunto_tentativo = _extrair_assunto(segmento.texto)
        if not assunto_tentativo or _parece_linha_de_rodape_ou_assinatura(assunto_tentativo):
            return None
    if categoria == "publicacao_oficial" and orgao and not (tipo or LINHA_ASSUNTO_RE.search(segmento.texto)):
        return None
    if categoria == "materia_jornalistica" and not (orgao or tipo or tem_cabecalho):
        return None
    if categoria == "materia_jornalistica" and (tipo or tem_cabecalho):
        categoria = "publicacao_oficial"

    trecho = _corrigir_ocr_basico(segmento.texto)
    assunto = _resumir_assunto(_extrair_assunto(segmento.texto))

    return {
        "pagina": segmento.pagina,
        "bloco": segmento.bloco,
        "categoria": categoria,
        "orgao": orgao,
        "tipo": tipo,
        "numero": numero,
        "data_documento": _extrair_data(segmento.texto),
        "assunto": assunto,
        "valor": _extrair_valor(segmento.texto),
        "trecho": trecho[:2000],
    }


def detectar(
    edicao_id: int,
    edicao_titulo: str,
    paginas: list[PageText],
) -> DetectionResult:
    trechos: list[dict] = []
    termos_encontrados: set[str] = set()
    paginas_com_mencao: set[int] = set()
    mencoes_db: list[dict] = []
    publicacoes: list[dict] = []

    termos = _termos()
    for pagina in paginas:
        for segmento in _segmentos(pagina):
            texto = segmento.texto or ""
            texto_norm = _sem_acentos(texto).casefold()
            termos_segmento: set[str] = set()

            for termo in termos:
                termo_norm = _sem_acentos(termo).casefold()
                start = 0
                while termo_norm and (idx := texto_norm.find(termo_norm, start)) != -1:
                    fim = idx + len(termo_norm)
                    if (
                        termo_norm in GENERIC_TERMS
                        and _contexto_ignorado_para_mencao_generica(texto, idx, fim)
                    ):
                        start = fim
                        continue
                    trecho = _snippet(texto, idx, fim)
                    paginas_com_mencao.add(pagina.pagina)
                    termos_encontrados.add(termo)
                    termos_segmento.add(termo)
                    trechos.append(
                        {
                            "pagina": pagina.pagina,
                            "bloco": segmento.bloco,
                            "trecho": trecho,
                        }
                    )
                    mencoes_db.append(
                        {"pagina": pagina.pagina, "trecho": trecho, "termo": termo}
                    )
                    start = fim

            for match in CEP_RE.finditer(texto):
                cep = match.group(0)
                if not _cep_de_inaja(cep):
                    continue
                if _contexto_ignorado_para_mencao_generica(
                    texto, match.start(), match.end()
                ):
                    continue
                trecho = _snippet(texto, match.start(), match.end())
                paginas_com_mencao.add(pagina.pagina)
                termos_encontrados.add(f"CEP {cep}")
                termos_segmento.add(f"CEP {cep}")
                trechos.append(
                    {
                        "pagina": pagina.pagina,
                        "bloco": segmento.bloco,
                        "trecho": trecho,
                    }
                )
                mencoes_db.append(
                    {"pagina": pagina.pagina, "trecho": trecho, "termo": f"CEP {cep}"}
                )

            publicacao = _publicacao_do_segmento(segmento, termos_segmento)
            if publicacao:
                publicacoes.append(publicacao)

    if publicacoes:
        import logging as _log
        _log.getLogger("detector").info("Chamando IA para refinar %s publicacoes...", len(publicacoes))
        publicacoes = refinar_publicacoes(publicacoes)
        _log.getLogger("detector").info("IA concluida. Publicacoes refinadas: %s", sum(1 for p in publicacoes if p.get("resumo_ia")))

    return DetectionResult(
        encontrado=bool(trechos or publicacoes),
        edicao_id=edicao_id,
        edicao_titulo=edicao_titulo,
        paginas_com_mencao=sorted(paginas_com_mencao),
        trechos=trechos,
        termos_encontrados=sorted(termos_encontrados),
        mencoes_db=mencoes_db,
        publicacoes=publicacoes,
    )
