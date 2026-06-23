"""Prometheus metrics (NFR-600).

Exposes a default-registry ``/metrics`` ASGI app plus lightweight HTTP request
instrumentation. Engine-specific metrics (scoring latency, decision counts, etc.)
get registered by their own modules as those land.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS_TOTAL = Counter(
    "cil_http_requests_total",
    "Total HTTP requests handled by the CIL API.",
    labelnames=("method", "path", "status"),
)

HTTP_REQUEST_DURATION = Histogram(
    "cil_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    labelnames=("method", "path"),
)


def render_metrics() -> Response:
    """Return the Prometheus exposition for the default registry (HTTP 200)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def prometheus_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Record request count + latency, labelled by route template (not raw path)."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start

    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)

    HTTP_REQUEST_DURATION.labels(request.method, path).observe(elapsed)
    HTTP_REQUESTS_TOTAL.labels(request.method, path, str(response.status_code)).inc()
    return response
