"""Middleware: security headers Django does not set, and the health check."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import add_never_cache_headers

Handler = Callable[[HttpRequest], HttpResponse]

# Snug needs none of these browser features. Denying them limits what any
# injected content could do, and tells the browser not to offer them.
PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "camera",
        "microphone",
        "geolocation",
        "payment",
        "usb",
        "serial",
        "bluetooth",
        "display-capture",
        "browsing-topics",
    )
)

HEALTH_PATH = "/healthz"


def extra_security_headers(get_response: Handler) -> Handler:
    def middleware(request: HttpRequest) -> HttpResponse:
        response = get_response(request)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        # Other sites may not load Snug's responses, such as rendered pages, as
        # images, scripts or styles of their own.
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response

    return middleware


def health_check(get_response: Handler) -> Handler:
    """Answer GET or HEAD /healthz before the host check.

    Docker and Caddy probe the container by its internal name, not the public
    domain, and Django would refuse those requests. The answer reveals
    nothing, and the security header middleware above this one still applies.
    Every other path keeps Django's strict host check.
    """

    def middleware(request: HttpRequest) -> HttpResponse:
        if request.path == HEALTH_PATH:
            if request.method not in ("GET", "HEAD"):
                response = HttpResponse(status=405, headers={"Allow": "GET, HEAD"})
            else:
                response = HttpResponse("ok\n", content_type="text/plain; charset=utf-8")
            add_never_cache_headers(response)
            return response
        return get_response(request)

    return middleware
