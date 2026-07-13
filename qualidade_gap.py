# -*- coding: utf-8 -*-
"""PR4/PR5b — merge multi-key, gap detection, digest, resumo operacional.

Importado por ``qualidade`` e pelo pipeline/agente/webapp.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config import SETTINGS

logger = logging.getLogger(__name__)


def _sem_acentos(texto: str) -> str:
    n = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in n if not unicodedata.combining(c)).casefold()


def _hash12(texto: str) -> str:
    return hashlib.sha1((texto or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


def _norm_key(s: object) -> str:
    return _sem_acentos(str(s or "")).strip()


def _keys_pub(pub: dict) -> dict[str, str]:
    pag = str(pub.get("pagina") or "")
    tipo = _norm_key(pub.get("tipo"))
    num = _norm_key(pub.get("numero"))
    trecho = pub.get("trecho") or ""
    h200 = _hash12(trecho[:200].casefold())
    h120 = _hash12(trecho[:120].casefold())
    return {
        "k1": f"{pag}|{tipo}|{num}|{h200}",
        "k2": f"{pag}|{tipo}|{h200}",
        "k3": f"{pag}|{tipo}|{num}",
        "k4": h120,
    }


def aplicar_merge_reprocess(
    novas: list[dict],
    snapshot: list[dict],
    *,
    preserve_feedback: bool | None = None,
    cols_publicacoes: set[str] | None = None,
) -> list[dict]:
    """Merge multi-key K1–K4: restaura feedback/IA/tentativas; numero novo prevalece."""
    if not snapshot:
        return [dict(p) for p in novas]
    if preserve_feedback is None:
        preserve_feedback = bool(
            getattr(SETTINGS, "quality_reprocess_preserve_feedback", True)
        )

    indices: dict[str, dict[str, list[dict]]] = {
        k: {} for k in ("k1", "k2", "k3", "k4")
    }
    for prev in snapshot:
        keys = _keys_pub(prev)
        for kn, kv in keys.items():
            if not kv:
                continue
            indices[kn].setdefault(kv, []).append(prev)

    used: set[int] = set()
    campos_ia = (
        "resumo_ia",
        "categoria_ia",
        "texto_corrigido",
        "orgao",
        "tipo",
        "data_documento",
        "assunto",
        "valor",
        "importancia",
        "importancia_motivo",
        "notificar_ia",
        "explicacao_ia",
        "partes_ia",
        "checklist_ia",
        "temas",
        "validacao_ia",
        "anomalia",
        "anomalia_motivo",
    )

    def match_prev(pub: dict) -> dict | None:
        keys = _keys_pub(pub)
        for kn in ("k1", "k2", "k3", "k4"):
            kv = keys[kn]
            for c in indices[kn].get(kv) or []:
                cid = id(c)
                if cid in used:
                    continue
                used.add(cid)
                return c
        return None

    out: list[dict] = []
    for pub in novas:
        merged = dict(pub)
        prev = match_prev(pub)
        if not prev:
            out.append(merged)
            continue
        for campo in campos_ia:
            if not merged.get(campo) and prev.get(campo) not in (None, ""):
                merged[campo] = prev[campo]
        if not merged.get("numero") and prev.get("numero"):
            merged["numero"] = prev["numero"]
        if preserve_feedback:
            if prev.get("feedback") and not merged.get("feedback"):
                merged["feedback"] = prev["feedback"]
            if prev.get("feedback_em") and not merged.get("feedback_em"):
                merged["feedback_em"] = prev["feedback_em"]
        if cols_publicacoes is None or "ia_tentativas" in cols_publicacoes:
            if prev.get("ia_tentativas") is not None and merged.get("ia_tentativas") is None:
                merged["ia_tentativas"] = prev.get("ia_tentativas")
        if cols_publicacoes is None or "ia_status" in cols_publicacoes:
            if prev.get("ia_status") and not merged.get("ia_status"):
                merged["ia_status"] = prev.get("ia_status")
        out.append(merged)
    return out


def _contar_hits_inaja(texto: str) -> tuple[int, int]:
    n = _sem_acentos(texto)
    hits = len(re.findall(r"inaja|inava", n))
    headers = len(
        re.findall(
            r"prefeitura municipal de ina|municipio de ina|"
            r"camara municipal de ina|prefeitura de ina",
            n,
        )
    )
    return hits, headers


def _ler_texto_edicao(edicao: dict) -> tuple[str, bool]:
    caminho = edicao.get("caminho_local") or ""
    txt_path = edicao.get("texto_extraido_path") or ""
    ocrj = False
    if caminho:
        p = Path(caminho)
        if not p.is_absolute():
            root = Path(getattr(SETTINGS, "download_dir", Path("edicoes")))
            for cand in (p, root / caminho, Path.cwd() / caminho):
                if cand.exists():
                    p = cand
                    break
        if p.suffix.lower() == ".pdf":
            ocrj = p.with_suffix(".ocr.json").exists()
            tp = p.with_suffix(".txt")
            if tp.exists():
                return tp.read_text(encoding="utf-8", errors="ignore"), ocrj
        elif p.suffix.lower() == ".txt" and p.exists():
            return p.read_text(encoding="utf-8", errors="ignore"), ocrj
    if txt_path:
        tp = Path(txt_path)
        if tp.exists():
            return tp.read_text(encoding="utf-8", errors="ignore"), ocrj
    return "", ocrj


def diagnosticar_gap_edicao(
    edicao: dict,
    *,
    n_pub: int | None = None,
    mode: str | None = None,
    min_hits: int | None = None,
    max_pubs_audit: int | None = None,
    paginas_ocr_fraco: int = 0,
    paginas_total: int = 0,
) -> dict[str, Any]:
    mode = (
        mode or getattr(SETTINGS, "quality_gap_mode", "reprocess") or "reprocess"
    ).casefold()
    H = int(
        min_hits
        if min_hits is not None
        else getattr(SETTINGS, "quality_gap_min_hits", 3) or 3
    )
    P = int(
        max_pubs_audit
        if max_pubs_audit is not None
        else getattr(SETTINGS, "quality_gap_max_pubs", 1) or 1
    )
    if n_pub is None:
        n_pub = int(edicao.get("n_pub") or edicao.get("n_pubs") or 0)
    texto, tem_ocrj = _ler_texto_edicao(edicao)
    hits, headers = _contar_hits_inaja(texto)

    hit = False
    headers_only = False
    if mode == "audit":
        if hits >= H and n_pub <= P:
            hit = True
        if headers >= 2 and n_pub < max(1, headers // 2):
            hit = True
            if not (hits >= H and n_pub <= P):
                headers_only = True
    else:
        if hits >= H and n_pub <= max(1, hits // 4):
            hit = True
        if headers >= 2 and n_pub < max(1, headers // 2):
            if not hit:
                headers_only = True
            hit = True
        if hits >= 2 and n_pub == 0:
            hit = True

    ratio = (paginas_ocr_fraco / paginas_total) if paginas_total > 0 else 0.0
    severidade = "none"
    acao = "none"
    if hit:
        if ratio >= 0.4 and hits >= H:
            severidade = "critical"
            acao = "force_ocr"
        elif headers_only:
            severidade = "low"
            acao = "redetect_cache" if tem_ocrj else "force_ocr"
        else:
            severidade = "under"
            acao = "redetect_cache" if tem_ocrj else "force_ocr"

    score = 0
    if severidade != "none":
        score = max(
            0,
            min(
                100,
                (hits - n_pub * 3) * 8
                + headers * 5
                + (25 if severidade == "critical" else 0),
            ),
        )

    return {
        "hits": hits,
        "headers": headers,
        "n_pub": n_pub,
        "tem_ocr_json": tem_ocrj,
        "severidade": severidade,
        "acao_sugerida": acao,
        "score_gap": score,
        "mode": mode,
    }


def avaliar_e_persistir_gap(edicao_id: int) -> dict | None:
    import database

    if not bool(getattr(SETTINGS, "quality_gap_detect", True)):
        return None
    with database.connect() as c:
        cols_e = {r[1] for r in c.execute("PRAGMA table_info(edicoes)").fetchall()}
        if "gap_severidade" not in cols_e:
            return None
        row = c.execute("SELECT * FROM edicoes WHERE id=?", (edicao_id,)).fetchone()
        if not row:
            return None
        ed = dict(row)
        n_pub = c.execute(
            "SELECT COUNT(*) FROM publicacoes WHERE edicao_id=?", (edicao_id,)
        ).fetchone()[0]
        fraco = total = 0
        try:
            m = c.execute(
                "SELECT paginas_ocr_fraco, paginas_total FROM deteccao_metricas "
                "WHERE edicao_id=?",
                (edicao_id,),
            ).fetchone()
            if m:
                fraco, total = int(m[0] or 0), int(m[1] or 0)
        except Exception:
            pass

    diag = diagnosticar_gap_edicao(
        ed,
        n_pub=n_pub,
        paginas_ocr_fraco=fraco,
        paginas_total=total,
    )
    status = "pendente" if diag["severidade"] != "none" else "ok"
    agora = datetime.now().isoformat(timespec="seconds")
    with database.connect() as c:
        c.execute(
            """
            UPDATE edicoes SET
              gap_severidade = ?,
              gap_score = ?,
              gap_hits = ?,
              gap_headers = ?,
              gap_acao = ?,
              gap_status = ?,
              gap_avaliado_em = ?,
              gap_detalhe = ?
            WHERE id = ?
            """,
            (
                diag["severidade"],
                diag["score_gap"],
                diag["hits"],
                diag["headers"],
                diag["acao_sugerida"],
                status,
                agora,
                f"mode={diag['mode']} n_pub={n_pub}",
                edicao_id,
            ),
        )
    diag["edicao_id"] = edicao_id
    diag["gap_status"] = status
    return diag


def resumo_operacional() -> dict[str, int]:
    import database

    out: dict[str, int] = {
        "fila_re_ia": 0,
        "gaps_pendentes": 0,
        "gaps_under": 0,
        "gaps_critical": 0,
        "confianca_revisar": 0,
        "confianca_alta": 0,
        "confianca_media": 0,
        "fn_so_mencao": 0,
        "anomalias": 0,
        "correcoes_ano": 0,
    }
    try:
        with database.connect() as c:
            cols_p = {
                r[1] for r in c.execute("PRAGMA table_info(publicacoes)").fetchall()
            }
            cols_e = {r[1] for r in c.execute("PRAGMA table_info(edicoes)").fetchall()}
            if "confianca_nivel" in cols_p:
                for nivel, key in (
                    ("revisar", "confianca_revisar"),
                    ("alta", "confianca_alta"),
                    ("media", "confianca_media"),
                ):
                    out[key] = c.execute(
                        "SELECT COUNT(*) FROM publicacoes "
                        "WHERE lower(COALESCE(confianca_nivel,''))=?",
                        (nivel,),
                    ).fetchone()[0]
            try:
                from qualidade import listar_candidatas_re_ia

                out["fila_re_ia"] = len(listar_candidatas_re_ia(80))
            except Exception:
                out["fila_re_ia"] = c.execute(
                    "SELECT COUNT(*) FROM publicacoes "
                    "WHERE resumo_ia IS NULL OR trim(resumo_ia)=''"
                ).fetchone()[0]
            if "gap_status" in cols_e:
                out["gaps_pendentes"] = c.execute(
                    "SELECT COUNT(*) FROM edicoes WHERE gap_status='pendente'"
                ).fetchone()[0]
                out["gaps_under"] = c.execute(
                    "SELECT COUNT(*) FROM edicoes WHERE gap_severidade='under'"
                ).fetchone()[0]
                out["gaps_critical"] = c.execute(
                    "SELECT COUNT(*) FROM edicoes WHERE gap_severidade='critical'"
                ).fetchone()[0]
            out["fn_so_mencao"] = c.execute(
                """
                SELECT COUNT(*) FROM edicoes e
                WHERE e.tem_inaja=1
                  AND NOT EXISTS (SELECT 1 FROM publicacoes p WHERE p.edicao_id=e.id)
                  AND (e.revisao_so_mencao IS NULL OR e.revisao_so_mencao=''
                       OR e.revisao_so_mencao='pendente')
                """
            ).fetchone()[0]
            if "anomalia" in cols_p:
                out["anomalias"] = c.execute(
                    "SELECT COUNT(*) FROM publicacoes WHERE anomalia=1"
                ).fetchone()[0]
            if "flags_qualidade" in cols_p:
                out["correcoes_ano"] = c.execute(
                    "SELECT COUNT(*) FROM publicacoes "
                    "WHERE flags_qualidade LIKE '%numero_corrigido%'"
                ).fetchone()[0]
    except Exception:
        logger.debug("resumo_operacional falhou", exc_info=True)
    return out


def montar_digest_qualidade(data: str | None = None) -> str:
    d = data or date.today().isoformat()
    r = resumo_operacional()
    try:
        from agente import modo_efetivo
        import database as _db

        modo = modo_efetivo()
        ult = _db.get_setting("agente_ultimo_cerebro", "") or "—"
    except Exception:
        modo, ult = "—", "—"
    return (
        f"=== DIGEST-QUALIDADE {d} ===\n"
        f"Publicações: alta={r['confianca_alta']} media={r['confianca_media']} "
        f"revisar={r['confianca_revisar']}\n"
        f"Fila re-IA: {r['fila_re_ia']}\n"
        f"Gaps pendentes: {r['gaps_pendentes']} "
        f"(under={r['gaps_under']} critical={r['gaps_critical']})\n"
        f"FN só-menção: {r['fn_so_mencao']}\n"
        f"Anomalias: {r['anomalias']}\n"
        f"Correções ano OCR (flags): {r['correcoes_ano']}\n"
        f"Agente: modo={modo} ultimo_cerebro={ult}\n"
    )


def gravar_digest_qualidade() -> Path | None:
    if not bool(getattr(SETTINGS, "quality_digest_diario", True)):
        return None
    texto = montar_digest_qualidade()
    alert_dir = SETTINGS.alert_dir
    alert_dir.mkdir(parents=True, exist_ok=True)
    path = alert_dir / f"{date.today().isoformat()}.log"
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + texto + "\n" + "-" * 40 + "\n")
    return path


def listar_gaps_pendentes(limit: int = 30) -> list[dict]:
    import database

    with database.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(edicoes)").fetchall()}
        if "gap_status" not in cols:
            return []
        rows = c.execute(
            """
            SELECT id, titulo, data_publicacao, gap_severidade, gap_score,
                   gap_hits, gap_headers, gap_acao, gap_status, gap_avaliado_em
            FROM edicoes
            WHERE gap_status = 'pendente'
            ORDER BY COALESCE(gap_score, 0) DESC, data_publicacao DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
