"""Testes para o middleware de autenticacao HTTP Basic (auth_middleware.py)."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient


def _client(db, mock_settings, monkeypatch, user: str, pwd: str) -> TestClient:
    object.__setattr__(mock_settings, "webapp_user", user)
    object.__setattr__(mock_settings, "webapp_password", pwd)
    import webapp
    import auth_middleware
    monkeypatch.setattr(auth_middleware, "SETTINGS", mock_settings)
    return TestClient(webapp.app)


def _auth_header(user: str, pwd: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {"Authorization": "Basic " + token}


def test_auth_desativada_libera_acesso(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch, "", "")
    assert client.get("/status").status_code == 200


def test_auth_ativada_bloqueia_sem_credencial(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch, "admin", "secret")
    resp = client.get("/status")
    assert resp.status_code == 401
    assert "Basic" in resp.headers.get("WWW-Authenticate", "")


@pytest.mark.parametrize(
    "user,pwd,esperado",
    [
        ("admin", "secret", 200),
        ("admin", "errada", 401),
        ("errado", "secret", 401),
    ],
)
def test_credencial_correta_e_errada(db, mock_settings, monkeypatch, user, pwd, esperado):
    client = _client(db, mock_settings, monkeypatch, "admin", "secret")
    resp = client.get("/status", headers=_auth_header(user, pwd))
    assert resp.status_code == esperado


def test_static_permanece_publico(db, mock_settings, monkeypatch):
    client = _client(db, mock_settings, monkeypatch, "admin", "secret")
    resp = client.get("/static/styles.css")
    assert resp.status_code != 401
