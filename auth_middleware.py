from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from config import SETTINGS


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        user = SETTINGS.webapp_user
        pwd = SETTINGS.webapp_password
        if not user and not pwd:
            return await call_next(request)
        if request.url.path.startswith("/static"):
            return await call_next(request)
        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            import base64
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                req_user, _, req_pwd = decoded.partition(":")
            except Exception:
                req_user, req_pwd = "", ""
            ok_user = secrets.compare_digest(req_user, user)
            ok_pwd = secrets.compare_digest(req_pwd, pwd)
            if ok_user and ok_pwd:
                return await call_next(request)
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": "Basic realm=Monitor"},
            content="Autenticacao necessaria.",
        )
