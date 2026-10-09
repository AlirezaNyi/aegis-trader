"""HTTP middleware for correlation IDs."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from aegis.logging import set_correlation_id

_CORR_HEADER = "x-correlation-id"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Bind ``X-Correlation-Id`` (or a new UUID) into the logging ContextVar."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(_CORR_HEADER)
        correlation_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())
        set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            set_correlation_id(None)
        response.headers[_CORR_HEADER] = correlation_id
        return response
