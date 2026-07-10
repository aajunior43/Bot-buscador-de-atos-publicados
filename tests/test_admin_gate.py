# -*- coding: utf-8 -*-
"""Porta do Admin (senha 1999) e APIs do agente."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client(db, mock_settings, monkeypatch):
    import config
    import database
    import webapp

    monkeypatch.setattr(config, "SETTINGS", mock_settings)
    monkeypatch.setattr(database, "SETTINGS", mock_settings)
    monkeypatch.setattr(webapp, "SETTINGS", mock_settings)
    database.init_db()
    return TestClient(webapp.app)


def _login(client: TestClient, senha: str = "1999"):
    return client.post("/admin/login", data={"senha": senha}, follow_redirects=False)


def test_admin_locked_without_cookie(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch)
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Senha do Admin" in r.text or "senha" in r.text.lower()


def test_admin_login_wrong(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch)
    r = client.post("/admin/login", data={"senha": "0000"})
    assert r.status_code == 401
    assert "incorreta" in r.text.lower() or "Senha" in r.text


def test_admin_login_ok_and_api(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch)
    r = _login(client)
    assert r.status_code in (303, 302)
    assert "monitor_admin_gate" in r.cookies

    # com cookie
    r2 = client.get("/admin")
    assert r2.status_code == 200
    assert "Agente de vigilância" in r2.text or "Agente" in r2.text
    assert 'name="smtp_from"' in r2.text
    assert 'name="absence_alert_days"' in r2.text

    r3 = client.get("/admin/api/agente/status")
    assert r3.status_code == 200
    data = r3.json()
    assert "ativo" in data
    assert "modo_config" in data


def test_admin_api_blocked_without_login(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch)
    r = client.get("/admin/api/agente/status")
    assert r.status_code == 401
    assert "detail" in r.json()


def test_admin_form_post_redirects_when_locked(db, mock_settings, monkeypatch):
    """Form POST sem cookie deve redirecionar ao login, não JSON cru."""
    client = _client(db, mock_settings, monkeypatch)
    r = client.post("/admin/backup", follow_redirects=False)
    assert r.status_code in (303, 302)
    assert r.headers.get("location", "").startswith("/admin")


def test_admin_api_agent_controls_require_gate(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch)
    for path in (
        "/admin/api/agente/on",
        "/admin/api/agente/off",
        "/admin/api/agente/pulse",
        "/admin/api/agente/cerebro",
        "/admin/api/agente/once",
        "/admin/api/ferramenta/lock",
    ):
        r = client.post(path)
        assert r.status_code == 401, path

    r_modo = client.post("/admin/api/agente/modo", json={"modo": "escudo"})
    assert r_modo.status_code == 401

    _login(client)
    r_on = client.post("/admin/api/agente/on")
    assert r_on.status_code == 200
    assert r_on.json().get("ok") is True


def test_admin_api_modo_invalid_json(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch)
    _login(client)
    r = client.post(
        "/admin/api/agente/modo",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_api_agente_resumo_public(db, mock_settings, monkeypatch):
    """Resumo do agente é público (chips do menu) — sem senha admin."""
    client = _client(db, mock_settings, monkeypatch)
    r = client.get("/api/agente/resumo")
    assert r.status_code == 200
    data = r.json()
    assert "ativo" in data

