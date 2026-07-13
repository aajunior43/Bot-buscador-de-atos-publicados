# -*- coding: utf-8 -*-
"""Qualidade de publicações: correção de ano OCR, flags e pós-processamento.

PR1: correção determinística de número/ano + flags_qualidade.
Hooks no pipeline (após detectar), sem alterar a assinatura de detector.detectar.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from config import SETTINGS

logger = logging.getLogger(__name__)

# Pares de dígitos frequentemente trocados pelo OCR (mapa do design).
_OCR_DIGIT_CONFUSIONS: dict[str, set[str]] = {
    "0": {"6", "8", "9"},
    "1": {"7"},
    "2": {"7", "8", "3"},
    "3": {"8", "9", "2"},
    "5": {"6", "8"},
    "6": {"0", "5"},
    "7": {"1", "2"},
    "8": {"0", "2", "3", "5", "9"},
    "9": {"0", "3", "8"},
}


@dataclass
class CorrecaoNumero:
    numero_original: str | None
    numero_final: str | None
    corrigido: bool
    confianca_correcao: float  # 0.0–1.0
    motivo: str
    precisa_revisao: bool


def _ano_ref(
    data_publicacao_edicao: str | None,
    data_documento: str | None,
) -> int:
    for raw in (data_publicacao_edicao, data_documento):
        if not raw:
            continue
        s = str(raw).strip()
        m = re.match(r"^(\d{4})", s)
        if m:
            try:
                y = int(m.group(1))
                if 1990 <= y <= 2100:
                    return y
            except ValueError:
                pass
        m = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", s)
        if m:
            try:
                y = int(m.group(3))
                if 1990 <= y <= 2100:
                    return y
            except ValueError:
                pass
        m = re.search(r"(\d{4})", s)
        if m:
            try:
                y = int(m.group(1))
                if 1990 <= y <= 2100:
                    return y
            except ValueError:
                pass
    return date.today().year


def _parse_seq_ano(numero: str) -> tuple[str, int] | None:
    m = re.match(r"^(\d{1,6})/(\d{4})$", numero.strip())
    if not m:
        return None
    try:
        return m.group(1), int(m.group(2))
    except ValueError:
        return None


def _candidatos_ano(
    ano: int,
    ano_ref: int,
    ano_max_futuro: int,
) -> list[tuple[int, float, int]]:
    """Gera (ano_cand, confianca, dist_ref) dentro da janela válida."""
    s = f"{ano:04d}"
    sref = f"{ano_ref:04d}"
    cands: set[int] = set()

    for i, ch in enumerate(s):
        for alt in _OCR_DIGIT_CONFUSIONS.get(ch, set()):
            cands.add(int(s[:i] + alt + s[i + 1 :]))

    # Hamming-1 em direção ao ano da edição (ex.: 2036 → 2026)
    if len(s) == len(sref):
        for i in range(len(s)):
            if s[i] != sref[i]:
                cands.add(int(s[:i] + sref[i] + s[i + 1 :]))

    lo = ano_ref - 15
    hi = ano_ref + max(0, ano_max_futuro)
    scored: list[tuple[int, float, int]] = []
    for c in cands:
        if c < lo or c > hi:
            continue
        dist = abs(c - ano_ref)
        if dist == 0:
            conf = 0.95
        elif dist <= 1:
            conf = 0.85
        else:
            conf = 0.55
        scored.append((c, conf, dist))
    scored.sort(key=lambda t: (t[2], -t[1], t[0]))
    return scored


def corrigir_numero_ano(
    numero: str | None,
    *,
    data_publicacao_edicao: str | None,
    data_documento: str | None = None,
    trecho: str = "",
    texto_corrigido: str = "",
    resumo_ia: str = "",
    ano_max_futuro: int | None = None,
) -> CorrecaoNumero:
    """Valida e corrige ano OCR impossível em números N/AAAA."""
    from detector import _numero_ancorado_no_texto, _numero_ato_valido

    original = (numero or "").strip() or None
    if ano_max_futuro is None:
        ano_max_futuro = int(getattr(SETTINGS, "quality_ano_max_futuro", 1) or 1)

    limpo = _numero_ato_valido(original) if original else None
    if not limpo:
        return CorrecaoNumero(
            numero_original=original,
            numero_final=None if not original else original,
            corrigido=False,
            confianca_correcao=0.0,
            motivo="sem_numero_valido",
            precisa_revisao=False,
        )

    parsed = _parse_seq_ano(limpo)
    if not parsed:
        return CorrecaoNumero(
            numero_original=original,
            numero_final=limpo,
            corrigido=False,
            confianca_correcao=1.0,
            motivo="sem_ano_4digitos",
            precisa_revisao=False,
        )

    seq, ano_num = parsed
    ref = _ano_ref(data_publicacao_edicao, data_documento)
    teto = ref + max(0, ano_max_futuro)

    if 1990 <= ano_num <= teto:
        return CorrecaoNumero(
            numero_original=original,
            numero_final=limpo,
            corrigido=False,
            confianca_correcao=1.0,
            motivo="ok",
            precisa_revisao=False,
        )

    # Ano futuro demais
    blob = "\n".join(
        x for x in (trecho or "", texto_corrigido or "", resumo_ia or "") if x
    )
    if blob.strip() and _numero_ancorado_no_texto(limpo, blob):
        return CorrecaoNumero(
            numero_original=original,
            numero_final=limpo,
            corrigido=False,
            confianca_correcao=0.4,
            motivo="ano_futuro_ancorado",
            precisa_revisao=True,
        )

    candidatos = _candidatos_ano(ano_num, ref, ano_max_futuro)
    bons = [c for c in candidatos if c[2] <= 1 and c[1] >= 0.8]
    if len(bons) == 1 or (
        bons and bons[0][2] < (bons[1][2] if len(bons) > 1 else 999)
    ):
        melhor = bons[0]
        ano_novo = melhor[0]
        final = f"{seq}/{ano_novo:04d}"
        return CorrecaoNumero(
            numero_original=original,
            numero_final=final,
            corrigido=final != limpo,
            confianca_correcao=melhor[1],
            motivo=f"ano_futuro_ocr:{ano_num}→{ano_novo}",
            precisa_revisao=False,
        )

    if ano_num > ref + 10:
        return CorrecaoNumero(
            numero_original=original,
            numero_final=None,
            corrigido=True,
            confianca_correcao=0.3,
            motivo=f"ano_absurdo_descartado:{ano_num}",
            precisa_revisao=True,
        )

    return CorrecaoNumero(
        numero_original=original,
        numero_final=limpo,
        corrigido=False,
        confianca_correcao=0.3,
        motivo=f"ano_futuro_sem_candidato:{ano_num}",
        precisa_revisao=True,
    )


def _merge_flags(pub: dict, *novas: str) -> list[Any]:
    raw = pub.get("flags_qualidade")
    flags: list[Any] = []
    if isinstance(raw, list):
        flags = list(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                flags = list(parsed)
            elif parsed:
                flags = [parsed]
        except json.JSONDecodeError:
            flags = [raw]
    for f in novas:
        if f and f not in flags:
            flags.append(f)
    return flags


def aplicar_correcao_numero_pub(
    pub: dict,
    *,
    data_edicao: str | None,
    ano_max_futuro: int | None = None,
) -> dict:
    """Aplica corrigir_numero_ano em um dict de publicação (mutável / cópia rasa)."""
    out = dict(pub)
    corr = corrigir_numero_ano(
        out.get("numero"),
        data_publicacao_edicao=data_edicao,
        data_documento=out.get("data_documento"),
        trecho=str(out.get("trecho") or ""),
        texto_corrigido=str(out.get("texto_corrigido") or ""),
        resumo_ia=str(out.get("resumo_ia") or ""),
        ano_max_futuro=ano_max_futuro,
    )
    if corr.corrigido or corr.precisa_revisao or corr.motivo not in ("ok", "sem_ano_4digitos", "sem_numero_valido"):
        flags = _merge_flags(out)
        if corr.corrigido:
            flags = _merge_flags({"flags_qualidade": flags}, f"numero_corrigido:{corr.motivo}")
            if corr.numero_original and corr.numero_final != corr.numero_original:
                out["numero_original_ocr"] = corr.numero_original
        if corr.precisa_revisao:
            flags = _merge_flags(
                {"flags_qualidade": flags},
                corr.motivo if corr.motivo else "precisa_revisao",
            )
            out["precisa_revisao_qualidade"] = True
        if flags:
            out["flags_qualidade"] = flags
    if corr.numero_final is not None or corr.corrigido:
        out["numero"] = corr.numero_final
    out["_correcao_numero"] = {
        "original": corr.numero_original,
        "final": corr.numero_final,
        "corrigido": corr.corrigido,
        "confianca": corr.confianca_correcao,
        "motivo": corr.motivo,
        "precisa_revisao": corr.precisa_revisao,
    }
    return out


# Tipos genéricos (design Feature 2/3)
_TIPOS_GENERICOS = frozenset(
    {
        "",
        "ato",
        "atos",
        "outros",
        "outro",
        "termo",
        "publicacao",
        "publicação",
        "n/a",
        "na",
        "nao identificado",
        "não identificado",
    }
)


def _sem_acentos(texto: str) -> str:
    import unicodedata

    n = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in n if not unicodedata.combining(c))


def _tipo_generico(tipo: str | None) -> bool:
    if not tipo or not str(tipo).strip():
        return True
    try:
        from ai_processor import normalizar_tipo_ato

        t = normalizar_tipo_ato(tipo) or tipo
    except Exception:
        t = tipo
    chave = _sem_acentos(str(t)).casefold().strip()
    return chave in _TIPOS_GENERICOS


def _orgao_class(orgao: str | None) -> str:
    """Retorna 'inaja' | 'vizinho' | 'generico' | 'vazio'."""
    if not orgao or not str(orgao).strip():
        return "vazio"
    from config import MUNICIPIOS_VIZINHOS

    o = _sem_acentos(str(orgao)).casefold()
    if any(v in o for v in MUNICIPIOS_VIZINHOS):
        # "inaja" not in neighbors list typically
        if "inaja" in o:
            return "inaja"
        return "vizinho"
    if "inaja" in o or "prefeitura" in o or "camara" in o or "municipio" in o or "conselho" in o:
        if "inaja" in o:
            return "inaja"
        # órgão sem nome do município mas típico municipal genérico
        return "generico"
    return "generico"


def _ano_numero_coerente(
    numero: str | None,
    data_edicao: str | None,
    *,
    ano_max_futuro: int = 1,
) -> bool | None:
    """True se N/AAAA coerente com edição; False se incoerente; None se sem ano."""
    from detector import _numero_ato_valido

    n = _numero_ato_valido(numero)
    if not n or "/" not in n:
        return None
    try:
        ano = int(n.split("/", 1)[1][:4])
    except (ValueError, IndexError):
        return None
    ref = _ano_ref(data_edicao, None)
    return 1990 <= ano <= ref + max(0, ano_max_futuro)


def _parse_validacao(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            p = json.loads(raw)
            return p if isinstance(p, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def calcular_confianca(
    pub: dict,
    *,
    data_edicao: str | None = None,
    alta_min: int | None = None,
    media_min: int | None = None,
) -> dict[str, Any]:
    """Score 0–100 de confiabilidade da extração + nivel alta|media|revisar."""
    from detector import _numero_ato_valido

    if alta_min is None:
        alta_min = int(getattr(SETTINGS, "quality_confianca_alta_min", 85) or 85)
    if media_min is None:
        media_min = int(getattr(SETTINGS, "quality_confianca_media_min", 55) or 55)
    ano_max = int(getattr(SETTINGS, "quality_ano_max_futuro", 1) or 1)

    componentes: dict[str, int] = {}
    motivos: list[str] = []
    overrides: list[str] = []

    # --- numero (max 20) ---
    num_raw = pub.get("numero")
    n = _numero_ato_valido(num_raw)
    precisa = bool(pub.get("precisa_revisao_qualidade"))
    flags = pub.get("flags_qualidade") or []
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except json.JSONDecodeError:
            flags = [flags]
    flag_txt = " ".join(str(f) for f in (flags if isinstance(flags, list) else []))
    if any(
        x in flag_txt
        for x in ("precisa_revisao", "ano_futuro_ancorado", "ano_futuro_sem")
    ):
        precisa = True

    if not n:
        componentes["numero"] = 0
        motivos.append("numero_ausente")
    elif precisa:
        componentes["numero"] = 8
        motivos.append("numero_precisa_revisao")
    elif "/" not in n:
        componentes["numero"] = 12
        motivos.append("numero_sem_ano")
    else:
        coer = _ano_numero_coerente(n, data_edicao, ano_max_futuro=ano_max)
        if coer is True:
            componentes["numero"] = 20
        elif coer is False:
            componentes["numero"] = 8
            motivos.append("numero_ano_incoerente")
        else:
            componentes["numero"] = 12

    # --- orgao (max 20) ---
    ocls = _orgao_class(pub.get("orgao"))
    if ocls == "inaja":
        componentes["orgao"] = 20
    elif ocls == "vizinho":
        componentes["orgao"] = 0
        motivos.append("orgao_vizinho")
    elif ocls == "generico":
        componentes["orgao"] = 8
        motivos.append("orgao_generico")
    else:
        componentes["orgao"] = 0
        motivos.append("orgao_vazio")

    # --- resumo_ia (max 15) ---
    resumo = (pub.get("resumo_ia") or "").strip()
    componentes["resumo_ia"] = 15 if resumo else 0
    if not resumo:
        motivos.append("sem_resumo_ia")

    # --- tipo (max 15) ---
    tipo = pub.get("tipo")
    if not tipo or not str(tipo).strip():
        componentes["tipo"] = 0
        motivos.append("tipo_vazio")
    elif _tipo_generico(tipo):
        componentes["tipo"] = 5
        motivos.append("tipo_generico")
    else:
        componentes["tipo"] = 15

    # --- data_coerente (max 15) ---
    data_doc = pub.get("data_documento")
    ref = _ano_ref(data_edicao, None)
    ano_doc = None
    if data_doc:
        # reusa parser de _ano_ref
        try:
            ano_doc = _ano_ref(None, str(data_doc))
            # se fallback today year when empty - check if data_doc had year
            if not re.search(r"\d{4}", str(data_doc)):
                ano_doc = None
        except Exception:
            ano_doc = None
    if ano_doc is not None and re.search(r"\d{4}", str(data_doc or "")):
        if ref - 15 <= ano_doc <= ref + 1:
            componentes["data_coerente"] = 15
        else:
            componentes["data_coerente"] = 0
            motivos.append("data_documento_incoerente")
    else:
        coer_n = _ano_numero_coerente(n, data_edicao, ano_max_futuro=ano_max)
        if coer_n is True:
            componentes["data_coerente"] = 8
        else:
            componentes["data_coerente"] = 0
            motivos.append("data_documento_ausente")

    # --- validacao (max 10) ---
    val = _parse_validacao(pub.get("validacao_ia"))
    if ocls == "vizinho":
        componentes["validacao"] = 0
        motivos.append("validacao_vizinho")
    elif val.get("ok") is False or val.get("grave"):
        componentes["validacao"] = 0
        motivos.append("validacao_grave")
    elif val.get("avisos") or val.get("leve") or flag_txt:
        # flags leves de qualidade
        if any("numero_corrigido" in str(f) for f in (flags if isinstance(flags, list) else [])):
            componentes["validacao"] = 5
            motivos.append("flags_leve_correcao")
        elif val:
            componentes["validacao"] = 5
        else:
            componentes["validacao"] = 10
    else:
        componentes["validacao"] = 10

    # --- trecho (max 5) ---
    trecho = str(pub.get("trecho") or "")
    lt = len(trecho)
    if lt >= 80:
        componentes["trecho"] = 5
    elif lt >= 20:
        componentes["trecho"] = 2
    else:
        componentes["trecho"] = 0
        motivos.append("trecho_curto")

    # floor 0 already; sum + clamp
    raw = sum(max(0, int(v)) for v in componentes.values())
    score = max(0, min(100, raw))

    # nivel base
    if score >= alta_min:
        nivel = "alta"
    elif score >= media_min:
        nivel = "media"
    else:
        nivel = "revisar"

    # overrides (ordem)
    feedback = (pub.get("feedback") or "").strip().casefold()
    if feedback == "errado":
        nivel = "revisar"
        score = min(score, 50)
        overrides.append("feedback_errado")
    elif precisa or "ano_futuro_ancorado" in flag_txt:
        nivel = "revisar"
        overrides.append("precisa_revisao_ou_ancora")
    else:
        anom = pub.get("anomalia") in (1, True, "1", "true")
        amot = (pub.get("anomalia_motivo") or "").casefold()
        if anom and any(t in amot for t in ("valor", "outlier", "inconsist")):
            nivel = "revisar"
            overrides.append("anomalia_critica")
        elif feedback == "correto":
            score = min(100, score + 10)
            overrides.append("feedback_correto_boost")
            if score >= alta_min:
                nivel = "alta"
            elif score >= media_min:
                nivel = "media"
            else:
                nivel = "revisar"

    return {
        "score": score,
        "nivel": nivel,
        "componentes": componentes,
        "motivos": motivos,
        "overrides": overrides,
    }


def aplicar_confianca_pub(pub: dict, *, data_edicao: str | None) -> dict:
    """Calcula confiança e grava campos no dict."""
    out = dict(pub)
    r = calcular_confianca(out, data_edicao=data_edicao)
    out["confianca"] = r["score"]
    out["confianca_nivel"] = r["nivel"]
    out["confianca_detalhe"] = {
        "componentes": r["componentes"],
        "motivos": r["motivos"],
        "overrides": r["overrides"],
    }
    return out


def pos_processar_publicacoes(
    publicacoes: list[dict],
    *,
    data_edicao: str | None,
) -> list[dict]:
    """Pós-processa lista de pubs: correção de ano + confiança."""
    if not publicacoes:
        return publicacoes

    fix_ano = bool(getattr(SETTINGS, "quality_fix_numero_ano", True))
    confianca_on = bool(getattr(SETTINGS, "quality_confianca", False))

    if not fix_ano and not confianca_on:
        return publicacoes

    out: list[dict] = []
    for pub in publicacoes:
        p = dict(pub)
        if fix_ano:
            p = aplicar_correcao_numero_pub(p, data_edicao=data_edicao)
        if confianca_on:
            p = aplicar_confianca_pub(p, data_edicao=data_edicao)
        out.append(p)
    return out


def flags_qualidade_para_db(flags: Any) -> str | None:
    """Serializa flags para coluna TEXT."""
    if flags is None:
        return None
    if isinstance(flags, str):
        return flags if flags.strip() else None
    try:
        return json.dumps(flags, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(flags)
