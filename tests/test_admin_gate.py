# -*- coding: utf-8 -*-
"""Porta do Admin (senha 1999) e APIs do agente."""
from __future__ import annotations

import hashlib

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
    r = client.post("/admin/login", data={"senha": "1999"}, follow_redirects=False)
    assert r.status_code in (303, 302)
    assert "monitor_admin_gate" in r.cookies

    # com cookie
    r2 = client.get("/admin")
    assert r2.status_code == 200
    assert "Agente de vigilância" in r2.text or "Agente" in r2.text

    r3 = client.get("/admin/api/agente/status")
    assert r3.status_code == 200
    data = r3.json()
    assert "ativo" in data
    assert "modo_config" in data


def test_admin_api_blocked_without_login(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch)
    r = client.get("/admin/api/agente/status")
    assert r.status_code == 401
