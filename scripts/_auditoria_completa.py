# -*- coding: utf-8 -*-
"""Auditoria completa dos resultados processados."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database

database.init_db()


def main() -> int:
    with database.connect() as c:
        print("=== CONTADORES GERAIS ===")
        for t in [
            "edicoes",
            "publicacoes",
            "mencoes",
            "jobs",
            "notificacoes",
            "deteccao_metricas",
        ]:
            n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n}")

        print("\n=== EDICOES OCR / INAJA ===")
        for r in c.execute(
            "SELECT ocr_processado, COUNT(*) n FROM edicoes GROUP BY ocr_processado"
        ):
            print(f"  ocr_processado={r[0]}: {r[1]}")
        for r in c.execute(
            "SELECT tem_inaja, COUNT(*) n FROM edicoes GROUP BY tem_inaja"
        ):
            print(f"  tem_inaja={r[0]}: {r[1]}")

        print("\n=== PROCESSADAS (ocr=1) RESUMO ===")
        rows = c.execute(
            """
            SELECT e.id, e.data_publicacao, e.titulo, e.tem_inaja,
                   (SELECT COUNT(*) FROM mencoes m WHERE m.edicao_id=e.id) n_men,
                   (SELECT COUNT(*) FROM publicacoes p WHERE p.edicao_id=e.id) n_pub,
                   e.caminho_local, e.texto_extraido_path,
                   e.falhas_processamento, e.ultima_falha_msg,
                   e.revisao_so_mencao, e.auditoria_so_mencao, e.fn_sugestao
            FROM edicoes e
            WHERE e.ocr_processado=1
            ORDER BY e.data_publicacao DESC
            """
        ).fetchall()
        print(f"  total ocr_processado=1: {len(rows)}")
        for r in rows:
            d = dict(r)
            print(
                f"  id={d['id']} {d['data_publicacao']} inaja={d['tem_inaja']} "
                f"men={d['n_men']} pub={d['n_pub']} fails={d['falhas_processamento'] or 0}"
            )

        print("\n=== INAJA SEM PUBLICACOES ===")
        so_mencao = [
            dict(r)
            for r in rows
            if r["tem_inaja"] and int(r["n_pub"] or 0) == 0
        ]
        print(f"  total: {len(so_mencao)}")
        for d in so_mencao:
            aud = (d.get("auditoria_so_mencao") or "")[:100]
            fn = (d.get("fn_sugestao") or "")[:80]
            print(
                f"  id={d['id']} {d['data_publicacao']} men={d['n_men']} "
                f"aud={aud!r} fn={fn!r}"
            )

        print("\n=== JOBS POR STATUS ===")
        for r in c.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY n DESC"
        ):
            print(f"  {r[0]}: {r[1]}")

        print("\n=== ULTIMOS JOBS COM ERRO ===")
        cols = [
            x[1]
            for x in c.execute("PRAGMA table_info(jobs)").fetchall()
        ]
        print(f"  colunas jobs: {cols}")
        # status comuns
        for st in ("erro", "falha", "failed", "error", "cancelado"):
            n = c.execute(
                "SELECT COUNT(*) FROM jobs WHERE lower(status)=?", (st,)
            ).fetchone()[0]
            if n:
                print(f"  status={st}: {n}")
        # mensagem de erro
        msg_col = "mensagem" if "mensagem" in cols else (
            "erro" if "erro" in cols else None
        )
        if msg_col:
            errs = c.execute(
                f"""
                SELECT id, edicao_id, status, etapa, substr({msg_col},1,180),
                       COALESCE(atualizado_em, iniciado_em, '')
                FROM jobs
                WHERE lower(status) IN ('erro','falha','failed','error')
                   OR {msg_col} LIKE '%Error%' OR {msg_col} LIKE '%erro%'
                   OR {msg_col} LIKE '%Traceback%' OR {msg_col} LIKE '%Exception%'
                ORDER BY id DESC LIMIT 25
                """
            ).fetchall()
            print(f"  amostra erros: {len(errs)}")
            for r in errs:
                print(
                    f"    job={r[0]} ed={r[1]} st={r[2]} etapa={r[3]} "
                    f"when={r[5]} msg={r[4]}"
                )
            stuck = c.execute(
                """
                SELECT id, edicao_id, status, etapa, mensagem,
                       iniciado_em, atualizado_em
                FROM jobs WHERE status='rodando'
                ORDER BY id DESC
                """
            ).fetchall()
            print(f"  jobs rodando (stuck?): {len(stuck)}")
            for r in stuck:
                print(
                    f"    job={r[0]} ed={r[1]} etapa={r[3]} "
                    f"ini={r[5]} upd={r[6]} msg={(r[4] or '')[:120]}"
                )

        print("\n=== EDICOES COM FALHAS ===")
        fails = c.execute(
            """
            SELECT id, data_publicacao, titulo, falhas_processamento,
                   substr(COALESCE(ultima_falha_msg,''),1,150)
            FROM edicoes
            WHERE COALESCE(falhas_processamento,0) > 0
            ORDER BY falhas_processamento DESC
            LIMIT 30
            """
        ).fetchall()
        print(f"  total com falhas>0 (top 30): {len(fails)}")
        for r in fails:
            print(f"  id={r[0]} {r[1]} fails={r[3]} msg={r[4]}")

        print("\n=== QUALIDADE PUBLICACOES (TODAS) ===")
        pubs = [
            dict(r)
            for r in c.execute(
                """
                SELECT p.*, e.data_publicacao, e.titulo as ed_titulo
                FROM publicacoes p
                JOIN edicoes e ON e.id = p.edicao_id
                ORDER BY e.data_publicacao DESC, p.id DESC
                """
            ).fetchall()
        ]
        print(f"  total: {len(pubs)}")
        campos = [
            "tipo",
            "numero",
            "orgao",
            "valor",
            "resumo_ia",
            "assunto",
            "data_documento",
        ]
        for camp in campos:
            if camp not in (pubs[0] if pubs else {}):
                # descobrir colunas reais
                continue
            vazios = sum(1 for p in pubs if not str(p.get(camp) or "").strip())
            print(f"  sem {camp}: {vazios}/{len(pubs)} ({100*vazios/max(1,len(pubs)):.0f}%)")

        pcols = [x[1] for x in c.execute("PRAGMA table_info(publicacoes)").fetchall()]
        print(f"  colunas publicacoes: {pcols}")

        # campos fracos
        def vazio(p, k):
            return not str(p.get(k) or "").strip()

        sem_num = [p for p in pubs if vazio(p, "numero")]
        sem_org = [p for p in pubs if vazio(p, "orgao")]
        sem_tipo = [p for p in pubs if vazio(p, "tipo") or p.get("tipo") in ("?", "Outros", "")]
        sem_ia = [p for p in pubs if vazio(p, "resumo_ia")]
        orgao_nao = [
            p
            for p in pubs
            if "não ident" in (p.get("orgao") or "").lower()
            or "nao ident" in (p.get("orgao") or "").lower()
            or (p.get("orgao") or "").strip() in ("", "Órgão não identificado")
        ]

        print(f"\n  sem numero: {len(sem_num)}")
        print(f"  sem/orgao fraco: {len(sem_org)} + nao-ident {len(orgao_nao)}")
        print(f"  tipo vazio/?/Outros: {len(sem_tipo)}")
        print(f"  sem resumo_ia: {len(sem_ia)}")

        print("\n=== TIPOS DE PUBLICACAO ===")
        tipos = Counter((p.get("tipo") or "(vazio)") for p in pubs)
        for t, n in tipos.most_common():
            print(f"  {n:3}  {t}")

        print("\n=== ORGAOS ===")
        orgs = Counter((p.get("orgao") or "(vazio)") for p in pubs)
        for o, n in orgs.most_common(20):
            print(f"  {n:3}  {o}")

        # Possíveis municípios vizinhos
        print("\n=== SUSPEITA MUNICIPIO VIZINHO NO TEXTO/ORGAO ===")
        viz = [
            "paranacity",
            "itaguaje",
            "itaguajé",
            "tapejara",
            "cruzeiro do sul",
            "uniflor",
            "jardim olinda",
            "santo antonio",
            "nova esperanca",
            "florai",
            "floraí",
            "lobato",
            "mandaguacu",
            "mandaguaçu",
        ]
        viz_hits = []
        for p in pubs:
            blob = " ".join(
                str(p.get(k) or "")
                for k in ("orgao", "resumo_ia", "assunto", "texto", "trecho", "conteudo")
            ).lower()
            for v in viz:
                if v in blob and "inajá" not in blob and "inaja" not in blob:
                    viz_hits.append((p["id"], p.get("data_publicacao"), v, p.get("orgao")))
                    break
        print(f"  possiveis vizinhos sem inaja no blob: {len(viz_hits)}")
        for h in viz_hits[:15]:
            print(f"    pub={h[0]} {h[1]} term={h[2]} orgao={h[3]}")

        # Duplicatas suspeitas
        print("\n=== DUPLICATAS SUSPEITAS (mesmo tipo+numero+orgao) ===")
        keys = Counter()
        by_key: dict[str, list] = {}
        for p in pubs:
            k = f"{(p.get('tipo') or '').strip()}|{(p.get('numero') or '').strip()}|{(p.get('orgao') or '').strip()}"
            if (p.get("numero") or "").strip():
                keys[k] += 1
                by_key.setdefault(k, []).append(p)
        dups = [(k, v) for k, v in keys.items() if v > 1]
        print(f"  grupos duplicados: {len(dups)}")
        for k, v in sorted(dups, key=lambda x: -x[1])[:20]:
            ids = [str(p["id"]) for p in by_key[k]]
            print(f"  x{v} {k}  ids={','.join(ids)}")

        # Métricas detecção
        print("\n=== METRICAS DETECCAO ===")
        mcols = [
            x[1]
            for x in c.execute("PRAGMA table_info(deteccao_metricas)").fetchall()
        ]
        print(f"  colunas: {mcols}")
        mets = c.execute("SELECT * FROM deteccao_metricas").fetchall()
        print(f"  rows: {len(mets)}")
        for r in mets[:25]:
            d = dict(r)
            print(
                f"  ed={d.get('edicao_id')} mencoes={d.get('mencoes')} "
                f"pubs={d.get('publicacoes')} viz={d.get('descartes_vizinho')} "
                f"ia_desc={d.get('descartes_ia')} ocr_fraco={d.get('paginas_ocr_fraco')}"
            )

        mq = database.get_metricas_qualidade()
        print("\n=== METRICAS AGREGADAS ===")
        for k, v in mq.items():
            print(f"  {k}: {v}")

        # Notificações falhas
        print("\n=== NOTIFICACOES ===")
        ncols = [
            x[1]
            for x in c.execute("PRAGMA table_info(notificacoes)").fetchall()
        ]
        print(f"  colunas: {ncols}")
        for r in c.execute(
            "SELECT status, COUNT(*) FROM notificacoes GROUP BY status"
            if "status" in ncols
            else "SELECT canal, COUNT(*) FROM notificacoes GROUP BY canal"
            if "canal" in ncols
            else "SELECT 1, COUNT(*) FROM notificacoes"
        ):
            print(f"  {r[0]}: {r[1]}")

        # Anomalias detalhadas
        print("\n=== ANOMALIAS DETALHADAS ===")
        try:
            anoms = database.listar_anomalias(limit=50)
            print(f"  total: {len(anoms)}")
            for a in anoms:
                d = dict(a) if hasattr(a, "keys") else a
                print(
                    f"  id={d.get('id')} ed={d.get('edicao_id')} "
                    f"tipo={d.get('tipo')} num={d.get('numero')} "
                    f"org={d.get('orgao')} cat={d.get('categoria')} "
                    f"imp={d.get('importancia')}"
                )
        except Exception as e:
            print(f"  erro: {e}")

        # Campos fracos detalhados (todas)
        print("\n=== PUBS FRACAS (sem numero OU sem resumo_ia) ===")
        ruins = [
            p
            for p in pubs
            if vazio(p, "numero") or vazio(p, "resumo_ia")
        ]
        print(f"  total: {len(ruins)}")
        for p in ruins:
            print(
                f"  [{p.get('data_publicacao')}] id={p['id']} ed={p.get('edicao_id')} "
                f"tipo={p.get('tipo') or '?'} num={p.get('numero') or '—'} "
                f"org={((p.get('orgao') or '')[:40])} "
                f"val={'sim' if p.get('valor') else 'nao'} "
                f"ia={'sim' if p.get('resumo_ia') else 'nao'}"
            )

        # OCR fraco / path ausente
        print("\n=== PATHS / ARQUIVOS ===")
        root = Path(__file__).resolve().parents[1]
        missing_pdf = 0
        missing_txt = 0
        missing_ocrj = 0
        for r in rows:
            d = dict(r)
            path = d.get("caminho_local") or ""
            if path:
                p = root / path if not Path(path).is_absolute() else Path(path)
                if not p.exists():
                    missing_pdf += 1
                    print(f"  PDF ausente id={d['id']}: {path}")
                else:
                    txt = p.with_suffix(".txt")
                    ocrj = p.with_suffix(".ocr.json")
                    if not txt.exists():
                        missing_txt += 1
                    if not ocrj.exists():
                        missing_ocrj += 1
            else:
                missing_pdf += 1
                print(f"  sem caminho_local id={d['id']}")
        print(
            f"  processadas: pdf_ausente={missing_pdf} "
            f"txt_ausente={missing_txt} ocrjson_ausente={missing_ocrj}"
        )

        # Ratio pubs/menções
        print("\n=== RATIO PUBS/MENCOES (processadas com inaja) ===")
        for r in rows:
            d = dict(r)
            if not d["tem_inaja"]:
                continue
            men = int(d["n_men"] or 0)
            pub = int(d["n_pub"] or 0)
            ratio = (pub / men) if men else 0
            flag = ""
            if men >= 5 and pub <= 1:
                flag = " << SUBDETECTADO?"
            elif men >= 3 and pub == 0:
                flag = " << FN?"
            print(
                f"  id={d['id']} {d['data_publicacao']} men={men} pub={pub} "
                f"ratio={ratio:.2f}{flag}"
            )

        # Valores estranhos
        print("\n=== VALORES SUSPEITOS ===")
        for p in pubs:
            val = str(p.get("valor") or "").strip()
            if not val:
                continue
            # valores absurdos ou malformados
            nums = re.findall(r"[\d.,]+", val)
            bad = False
            for n in nums:
                try:
                    x = float(n.replace(".", "").replace(",", "."))
                    if x > 1e10 or (x == 0 and len(val) < 5):
                        bad = True
                except ValueError:
                    pass
            if bad or len(val) > 80:
                print(f"  pub={p['id']} valor={val[:100]!r}")

        # Logs recentes de erro
        print("\n=== LOG MONITOR (erros recentes) ===")
        log = root / "logs" / "monitor.log"
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
            err_lines = [
                ln
                for ln in lines
                if re.search(r"ERROR|CRITICAL|Traceback|Exception", ln, re.I)
            ]
            print(f"  total linhas erro no log atual: {len(err_lines)}")
            for ln in err_lines[-30:]:
                print(f"  {ln[:200]}")
        else:
            print("  monitor.log ausente")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
