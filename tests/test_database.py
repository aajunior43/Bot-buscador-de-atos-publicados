"""
Testes unitários para database.py
Cobre init_db, insert/get operações, cleanup_stuck_jobs, notificações e webhooks.
"""
from __future__ import annotations

import time
import pytest


class TestInitDb:
    def test_init_cria_tabelas(self, db, mock_settings):
        import sqlite3
        conn = sqlite3.connect(mock_settings.db_path)
        tabelas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "edicoes" in tabelas
        assert "mencoes" in tabelas
        assert "publicacoes" in tabelas
        assert "jobs" in tabelas
        assert "notificacoes" in tabelas
        assert "settings" in tabelas
        assert "webhooks" in tabelas
        assert "deteccao_metricas" in tabelas
        assert "schema_migrations" in tabelas

    def test_migracoes_registradas(self, db, mock_settings):
        import sqlite3
        import database
        conn = sqlite3.connect(mock_settings.db_path)
        versoes = {
            r[0]
            for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        conn.close()
        for version, _ in database._MIGRATIONS:
            assert version in versoes


class TestInsertOrGetEdicao:
    def test_insere_nova(self, db):
        import database
        database.init_db()
        eid = database.insert_or_get_edicao("https://example.com/ed1.pdf", "Edição 1", "2026-01-15")
        assert isinstance(eid, int)
        assert eid > 0

    def test_idempotente(self, db):
        import database
        database.init_db()
        eid1 = database.insert_or_get_edicao("https://example.com/ed2.pdf", "Edição 2", "2026-01-20")
        eid2 = database.insert_or_get_edicao("https://example.com/ed2.pdf", "Edição 2", "2026-01-20")
        assert eid1 == eid2

    def test_url_existe(self, db):
        import database
        database.init_db()
        database.insert_or_get_edicao("https://example.com/ed3.pdf", "Edição 3", "2026-02-01")
        assert database.url_exists("https://example.com/ed3.pdf") is True
        assert database.url_exists("https://example.com/inexistente.pdf") is False


class TestInsertMencoes:
    def test_insere_mencoes(self, db):
        import database
        database.init_db()
        eid = database.insert_or_get_edicao("https://example.com/ed4.pdf", "Ed4", "2026-03-01")
        mencoes = [
            {"pagina": 1, "trecho": "...Inajá detectado...", "termo": "Inajá"},
            {"pagina": 2, "trecho": "...Prefeitura de Inajá...", "termo": "Prefeitura de Inajá"},
        ]
        database.insert_mencoes(eid, mencoes)
        import sqlite3
        conn = sqlite3.connect(database.SETTINGS.db_path)
        count = conn.execute("SELECT COUNT(*) FROM mencoes WHERE edicao_id=?", (eid,)).fetchone()[0]
        conn.close()
        assert count == 2

    def test_deduplicacao_por_hash(self, db):
        import database
        database.init_db()
        eid = database.insert_or_get_edicao("https://example.com/ed5.pdf", "Ed5", "2026-04-01")
        mencoes = [{"pagina": 1, "trecho": "...Inajá...", "termo": "Inajá"}]
        database.insert_mencoes(eid, mencoes)
        database.insert_mencoes(eid, mencoes)  # segunda inserção não deve duplicar
        import sqlite3
        conn = sqlite3.connect(database.SETTINGS.db_path)
        count = conn.execute("SELECT COUNT(*) FROM mencoes WHERE edicao_id=?", (eid,)).fetchone()[0]
        conn.close()
        assert count == 1  # deduplica pelo hash_trecho


class TestSomaValoresDedup:
    def test_deduplica_mesmo_numero(self, db):
        import database

        database.init_db()
        eid = database.insert_or_get_edicao(
            "https://example.com/val.pdf", "Val", "2026-06-01"
        )
        database.insert_publicacoes(
            eid,
            [
                {
                    "pagina": 1,
                    "categoria": "publicacao_oficial",
                    "orgao": "Prefeitura Municipal de Inajá",
                    "tipo": "Extrato de Contrato",
                    "numero": "030/2026",
                    "valor": "R$ 1.000.000,00",
                    "trecho": "a",
                },
                {
                    "pagina": 2,
                    "categoria": "publicacao_oficial",
                    "orgao": "Prefeitura Municipal de Inajá",
                    "tipo": "Extrato de Contrato",
                    "numero": "030/2026",
                    "valor": "R$ 1.000.000,00",
                    "trecho": "b",
                },
                {
                    "pagina": 3,
                    "categoria": "publicacao_oficial",
                    "orgao": "Prefeitura Municipal de Inajá",
                    "tipo": "Portaria",
                    "numero": "1/2026",
                    "valor": "R$ 500,00",
                    "trecho": "c",
                },
            ],
        )
        bruto = database.somar_valores_publicacoes(deduplicar=False)
        dedup = database.somar_valores_publicacoes(deduplicar=True)
        assert bruto["total"] == 2_000_500.0
        assert dedup["total"] == 1_000_500.0
        assert dedup["n_unicos"] == 2


class TestMetricasDeteccao:
    def test_salvar_e_agregar(self, db):
        import database
        from detector import DetectionMetrics

        database.init_db()
        eid = database.insert_or_get_edicao(
            "https://example.com/met.pdf", "Met", "2026-07-01"
        )
        database.salvar_metricas_deteccao(
            eid,
            DetectionMetrics(
                publicacoes_brutas=5,
                publicacoes_finais=3,
                descartes_ia=1,
                descartes_vizinho=1,
                paginas_total=20,
                paginas_ocr_fraco=4,
                mencoes=7,
            ),
        )
        m = database.get_metricas_qualidade()
        assert m["publicacoes_brutas"] == 5
        assert m["publicacoes_finais"] == 3
        assert m["descartes_vizinho"] == 1
        assert m["taxa_ocr_fraco_pct"] == 20.0
        assert m["edicoes_com_metricas"] == 1


class TestCleanupStuckJobs:
    def test_nao_limpa_jobs_recentes(self, db):
        import database
        database.init_db()
        eid = database.insert_or_get_edicao("https://example.com/stuck1.pdf", "Stuck1", "2026-05-01")
        jid = database.start_job("rodando OCR", edicao_id=eid)
        removidos = database.cleanup_stuck_jobs(max_hours=2)
        assert removidos == 0  # job recente não deve ser removido

    def test_limpa_jobs_antigos(self, db):
        import database
        import sqlite3
        database.init_db()
        eid = database.insert_or_get_edicao("https://example.com/stuck2.pdf", "Stuck2", "2026-05-02")
        jid = database.start_job("rodando OCR", edicao_id=eid)
        # Forçar atualizado_em para o passado
        conn = sqlite3.connect(database.SETTINGS.db_path)
        conn.execute(
            "UPDATE jobs SET atualizado_em = datetime('now', '-3 hours') WHERE id = ?",
            (jid,)
        )
        conn.commit()
        conn.close()
        removidos = database.cleanup_stuck_jobs(max_hours=2)
        assert removidos == 1


class TestNotificacoes:
    def test_insert_e_get_notificacao(self, db):
        import database
        database.init_db()
        eid = database.insert_or_get_edicao("https://example.com/notif1.pdf", "Notif1", "2026-06-01")
        database.insert_notificacao(eid, "telegram", "Mensagem de teste", sucesso=True)
        notifs = database.get_notificacoes(limit=10)
        assert len(notifs) >= 1
        assert notifs[0]["canal"] == "telegram"

    def test_notificacao_falha(self, db):
        import database
        database.init_db()
        database.insert_notificacao(None, "email", "Falha ao enviar", sucesso=False, erro="SMTP error")
        notifs = database.get_notificacoes(limit=10)
        falhas = [n for n in notifs if n["sucesso"] == 0]
        assert len(falhas) >= 1


class TestWebhooks:
    def test_upsert_e_get_webhooks(self, db):
        import database
        database.init_db()
        database.upsert_webhook("https://hooks.example.com/test", "Teste", ativo=True)
        webhooks = database.get_webhooks()
        assert any(wh["url"] == "https://hooks.example.com/test" for wh in webhooks)

    def test_delete_webhook(self, db):
        import database
        database.init_db()
        database.upsert_webhook("https://hooks.example.com/del", "Del", ativo=True)
        webhooks = database.get_webhooks()
        wh_id = next(wh["id"] for wh in webhooks if wh["url"] == "https://hooks.example.com/del")
        database.delete_webhook(wh_id)
        webhooks_after = database.get_webhooks()
        assert not any(wh["url"] == "https://hooks.example.com/del" for wh in webhooks_after)


class TestAusenciaPublicacao:
    def test_sem_publicacao_recente(self, db):
        import database
        database.init_db()
        # Banco vazio → nenhuma publicação com Inajá
        assert database.get_absence_alert_needed(days=30) is True

    def test_com_publicacao_recente(self, db):
        import database
        import sqlite3
        database.init_db()
        eid = database.insert_or_get_edicao("https://example.com/recente.pdf", "Recente", "2026-06-25")
        conn = sqlite3.connect(database.SETTINGS.db_path)
        conn.execute("UPDATE edicoes SET tem_inaja=1 WHERE id=?", (eid,))
        conn.commit()
        conn.close()
        assert database.get_absence_alert_needed(days=30) is False


class TestSettings:
    def test_set_e_get_setting(self, db):
        import database
        database.init_db()
        database.set_setting("chave_teste", "valor_teste")
        assert database.get_setting("chave_teste") == "valor_teste"

    def test_get_setting_padrao(self, db):
        import database
        database.init_db()
        assert database.get_setting("chave_inexistente", "padrão") == "padrão"

    def test_overwrite_setting(self, db):
        import database
        database.init_db()
        database.set_setting("chave_ow", "v1")
        database.set_setting("chave_ow", "v2")
        assert database.get_setting("chave_ow") == "v2"
