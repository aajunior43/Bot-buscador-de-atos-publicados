# -*- coding: utf-8 -*-
"""Testes PR2 — score de confiança (goldens G1–G8)."""
from __future__ import annotations

from unittest.mock import patch

from qualidade import calcular_confianca, pos_processar_publicacoes


def _calc(pub: dict, data_edicao: str = "2026-07-01", **settings_kw):
    from config import SETTINGS

    # usa SETTINGS real com patches se necessário
    return calcular_confianca(pub, data_edicao=data_edicao)


class TestGoldens:
    def test_g1_decreto_completo_alta(self):
        pub = {
            "tipo": "Decreto",
            "numero": "10/2026",
            "orgao": "Prefeitura Municipal de Inajá",
            "resumo_ia": "Decreto que regulamenta procedimento administrativo municipal.",
            "data_documento": "01/06/2026",
            "trecho": "x" * 200,
            "validacao_ia": {"ok": True},
        }
        r = _calc(pub)
        assert r["score"] >= 85
        assert r["nivel"] == "alta"
        assert 90 <= r["score"] <= 100 or r["score"] >= 85

    def test_g2_contrato_sem_numero_media(self):
        pub = {
            "tipo": "Contrato",
            "numero": None,
            "orgao": "Município de Inajá",
            "resumo_ia": "Contrato de prestação de serviços.",
            "valor": "R$ 10.000,00",
            "trecho": "x" * 120,
        }
        r = _calc(pub)
        assert 55 <= r["score"] <= 84
        assert r["nivel"] == "media"

    def test_g3_fraco_revisar(self):
        pub = {
            "tipo": "Outros",
            "numero": None,
            "orgao": None,
            "resumo_ia": None,
            "trecho": "curto",
        }
        r = _calc(pub)
        assert r["score"] <= 25
        assert r["nivel"] == "revisar"

    def test_g4_corrigido_2036_alta(self, mock_settings):
        pubs = [
            {
                "tipo": "Extrato de Contrato",
                "numero": "095/2036",
                "orgao": "Prefeitura Municipal de Inajá",
                "resumo_ia": "Extrato de contrato para aquisição de materiais.",
                "data_documento": "15/06/2026",
                "trecho": "EXTRATO contrato 095/2036 valor total " + ("y" * 100),
                "validacao_ia": {"ok": True},
            }
        ]
        with patch("qualidade.SETTINGS", mock_settings):
            out = pos_processar_publicacoes(pubs, data_edicao="2026-07-01")
        assert out[0]["numero"] == "095/2026"
        assert out[0]["confianca"] >= 85
        assert out[0]["confianca_nivel"] == "alta"

    def test_g5_ancorado_override_revisar(self, mock_settings):
        pubs = [
            {
                "tipo": "Portaria",
                "numero": "089/2036",
                "orgao": "Prefeitura Municipal de Inajá",
                "resumo_ia": "Portaria de nomeação.",
                "trecho": "Portaria Nº 089/2036 do município " + ("z" * 80),
            }
        ]
        with patch("qualidade.SETTINGS", mock_settings):
            out = pos_processar_publicacoes(pubs, data_edicao="2026-06-01")
        assert out[0]["numero"] == "089/2036"
        assert out[0]["confianca_nivel"] == "revisar"
        assert "precisa_revisao" in str(out[0].get("flags_qualidade") or "") or out[
            0
        ].get("precisa_revisao_qualidade")

    def test_g6_vizinho_baixo(self):
        pub = {
            "tipo": "Decreto",
            "numero": "01/2026",
            "orgao": "Prefeitura Municipal de Paranacity",
            "resumo_ia": "Decreto local.",
            "trecho": "x" * 100,
        }
        r = _calc(pub)
        assert r["componentes"]["orgao"] == 0
        assert r["nivel"] == "revisar" or r["score"] < 85

    def test_g7_ato_sem_numero_revisar(self):
        pub = {
            "tipo": "Ato",
            "numero": None,
            "orgao": "Prefeitura Municipal de Inajá",
            "trecho": "x" * 50,
        }
        r = _calc(pub)
        assert r["score"] < 55
        assert r["nivel"] == "revisar"

    def test_g8_extrato_completo_alta(self):
        pub = {
            "tipo": "Extrato de Contrato",
            "numero": "04/2026",
            "orgao": "Prefeitura Municipal de Inajá",
            "resumo_ia": "Extrato do Contrato nº 04/2026 para aquisição de veículos.",
            "data_documento": "07/07/2026",
            "trecho": "PREFEITURA MUNICIPAL DE INAJÁ EXTRATO " + ("w" * 150),
            "validacao_ia": {"ok": True},
            "checklist_ia": {"score": 90},
        }
        r = _calc(pub)
        assert r["score"] >= 85
        assert r["nivel"] == "alta"


class TestOverrides:
    def test_feedback_errado_forca_revisar(self):
        pub = {
            "tipo": "Decreto",
            "numero": "10/2026",
            "orgao": "Prefeitura Municipal de Inajá",
            "resumo_ia": "ok",
            "trecho": "x" * 100,
            "feedback": "errado",
        }
        r = _calc(pub)
        assert r["nivel"] == "revisar"
        assert "feedback_errado" in r["overrides"]


class TestInsertConfianca:
    def test_insert_persiste_confianca(self, db, mock_settings):
        import database

        database.init_db()
        eid = database.insert_or_get_edicao(
            "https://example.com/c1.pdf", "C1", "2026-07-01"
        )
        database.insert_publicacoes(
            eid,
            [
                {
                    "pagina": 1,
                    "tipo": "Decreto",
                    "numero": "1/2026",
                    "orgao": "Prefeitura Municipal de Inajá",
                    "trecho": "x",
                    "confianca": 92,
                    "confianca_nivel": "alta",
                    "confianca_detalhe": {"componentes": {"numero": 20}},
                }
            ],
        )
        with database.connect() as c:
            row = c.execute(
                "SELECT confianca, confianca_nivel FROM publicacoes WHERE edicao_id=?",
                (eid,),
            ).fetchone()
        assert row["confianca"] == 92
        assert row["confianca_nivel"] == "alta"
