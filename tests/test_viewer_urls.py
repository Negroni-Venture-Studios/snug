"""Every viewer URL goes through the authorisation path, and carries no database key.

These tests walk the URL configuration rather than a list kept by hand, so a
new viewer page that forgets the check fails here, not in production.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

from snug.documents.models import Document
from snug.links import service
from snug.viewer.views import CHECK_ATTRIBUTE

# Gate pages start a session, so they can only check the link itself. Any
# other viewer page must call authorise(). Adding to this set needs a reason.
GATES = {"viewer:send_code", "viewer:verify_code", "viewer:open"}
# Routes outside admin/ and v/. A viewer page anywhere else would escape the
# checks below, so nothing may be added here lightly.
# The root redirect; the founder's colour and logo.
OTHER_ROUTES = {"", "brand/brand.css", "brand/<str:name>"}
ALLOWED_PARAMETERS = {"token", "document_token", "page", "width"}
# Integers only for positions within the document the token grants.
INTEGER_PARAMETERS = {"page", "width"}


def walk(
    patterns: list[Any] | None = None, prefix: str = "", namespace: str = ""
) -> Iterator[tuple[str, str, URLPattern]]:
    """Yield (route, name, pattern) for every URL Snug serves."""
    for entry in get_resolver().url_patterns if patterns is None else patterns:
        if isinstance(entry, URLResolver):
            inner = f"{namespace}{entry.namespace}:" if entry.namespace else namespace
            yield from walk(entry.url_patterns, prefix + str(entry.pattern), inner)
        else:
            name = f"{namespace}{entry.name}" if entry.name else ""
            yield prefix + str(entry.pattern), name, entry


VIEWER = [(route, name, pattern) for route, name, pattern in walk() if route.startswith("v/")]


def test_there_are_viewer_urls_to_check() -> None:
    assert {name for _, name, _ in VIEWER} >= {"viewer:entry", *GATES}


@pytest.mark.parametrize(("route", "name", "pattern"), VIEWER, ids=[n for _, n, _ in VIEWER])
def test_every_viewer_url_is_checked(route: str, name: str, pattern: URLPattern) -> None:
    check = getattr(pattern.callback, CHECK_ATTRIBUTE, None)
    expected = "find_link" if name in GATES else "authorise"
    assert check == expected, f"{route} ({name}) is not wrapped by viewer_content or viewer_gate"


@pytest.mark.parametrize(("route", "name", "pattern"), VIEWER, ids=[n for _, n, _ in VIEWER])
def test_viewer_urls_carry_no_database_keys(route: str, name: str, pattern: URLPattern) -> None:
    converters = pattern.pattern.converters
    assert set(converters) <= ALLOWED_PARAMETERS, route
    for parameter, converter in converters.items():
        expected = "IntConverter" if parameter in INTEGER_PARAMETERS else "StringConverter"
        assert type(converter).__name__ == expected, route


def test_nothing_is_served_outside_admin_and_the_viewer() -> None:
    others = {route for route, _, _ in walk() if not route.startswith(("admin/", "v/"))}
    assert others == OTHER_ROUTES


@pytest.mark.django_db
@pytest.mark.parametrize(("route", "name", "pattern"), VIEWER, ids=[n for _, n, _ in VIEWER])
def test_no_viewer_url_shows_a_document_without_a_session(
    route: str, name: str, pattern: URLPattern
) -> None:
    document = Document.objects.create(title="Seed deck")
    issued = service.create(
        document=document,
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )
    url = "/" + (
        route.replace("<str:token>", issued.token)
        .replace("<str:document_token>", document.token)
        .replace("<int:page>", "1")
        .replace("<int:width>", "800")
    )
    browser = Client()
    for method in (browser.get, browser.post):
        response = method(url, secure=True)
        used = {template.name for template in response.templates}
        shown = f"{method.__name__.upper()} {url}"
        assert "viewer/reader.html" not in used, shown
        assert not response.get("Content-Type", "").startswith("image/"), shown
