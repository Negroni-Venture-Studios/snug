"""Every response carries Snug's security headers, and no CORS headers."""

import pytest
from django.conf import settings
from django.test import Client

EXPECTED_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self'; font-src 'self'; connect-src 'self'; manifest-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)

PATHS = ["/healthz", "/no-such-page"]


@pytest.mark.parametrize("path", PATHS)
def test_strict_headers_on_every_response(client: Client, path: str) -> None:
    response = client.get(path, secure=True)
    assert response.headers["Content-Security-Policy"] == EXPECTED_CSP
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]


@pytest.mark.parametrize("method", ["get", "options"])
@pytest.mark.parametrize("path", PATHS)
def test_no_cors_headers_even_when_asked(client: Client, method: str, path: str) -> None:
    response = getattr(client, method)(
        path,
        secure=True,
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]


def test_health_check(client: Client) -> None:
    response = client.get("/healthz", secure=True)
    assert response.status_code == 200
    assert response.content == b"ok\n"
    assert "no-cache" in response.headers["Cache-Control"]


def test_health_check_refuses_writes(client: Client) -> None:
    response = client.post("/healthz", secure=True)
    assert response.status_code == 405
    assert response.headers["Allow"] == "GET, HEAD"


def test_unknown_hosts_are_refused(client: Client) -> None:
    response = client.get("/", secure=True, headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert response.headers["Content-Security-Policy"] == EXPECTED_CSP


def test_health_check_answers_internal_host_names(client: Client) -> None:
    # Docker and Caddy probe the container by its internal name.
    response = client.get("/healthz", headers={"Host": "snug:8000"})
    assert response.status_code == 200
    assert response.content == b"ok\n"
    assert response.headers["Content-Security-Policy"] == EXPECTED_CSP


def test_health_check_head(client: Client) -> None:
    assert client.head("/healthz", secure=True).status_code == 200


def test_cookies_are_host_only_secure_and_hidden_from_scripts() -> None:
    assert settings.SESSION_COOKIE_NAME == "__Host-snug-session"
    assert settings.CSRF_COOKIE_NAME == "__Host-snug-csrf"
    assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
    assert settings.SESSION_COOKIE_HTTPONLY and settings.CSRF_COOKIE_HTTPONLY
    assert settings.CSRF_COOKIE_SAMESITE == "Strict"


def test_django_admin_is_not_installed() -> None:
    assert "django.contrib.admin" not in settings.INSTALLED_APPS


# Caddy sets X-Forwarded-Proto. From anyone else it is ignored, as
# X-Forwarded-For is (snug.core.client), so a request over plain HTTP cannot
# pass for HTTPS. Django sends HSTS only on a request it believes is secure.
VISITOR = "203.0.113.9"


def test_forwarded_proto_from_a_visitor_is_ignored() -> None:
    response = Client(REMOTE_ADDR=VISITOR).get("/healthz", headers={"X-Forwarded-Proto": "https"})
    assert response.status_code == 200
    assert "Strict-Transport-Security" not in response.headers


def test_forwarded_proto_from_the_proxy_is_believed() -> None:
    # With no SNUG_TRUSTED_PROXIES, the loopback addresses are the trusted proxies.
    response = Client(REMOTE_ADDR="127.0.0.1").get(
        "/healthz", headers={"X-Forwarded-Proto": "https"}
    )
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_the_forwarded_proto_check_runs_before_anything_reads_it() -> None:
    assert settings.MIDDLEWARE[0] == "snug.core.middleware.forwarded_proto"
