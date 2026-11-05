"""The audit log in the admin: newest first, filterable, and exportable as CSV."""

import csv
import io
import time
from typing import Any

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def streamed(response: Any) -> bytes:
    """The body of a streaming response, such as the CSV export."""
    return b"".join(response.streaming_content)


@pytest.fixture
def events() -> None:
    record(
        "admin.login",
        actor_type=AuditEvent.ActorType.ADMIN,
        actor="ruth@fernlea.example",
        ip_address="198.51.100.7",
    )
    record(
        "link.created",
        actor_type=AuditEvent.ActorType.ADMIN,
        actor="ruth@fernlea.example",
        target="priya@northfield.example",
        details={"link": "L-7Q2K"},
    )
    record(
        "viewer.code_refused",
        actor_type=AuditEvent.ActorType.VIEWER,
        actor='=HYPERLINK("http://evil.example")',
        target="priya@northfield.example",
        details={"reason": "wrong_email"},
    )
    record(
        "viewer.verified",
        actor_type=AuditEvent.ActorType.VIEWER,
        actor="priya@northfield.example",
        target="priya@northfield.example",
    )


@pytest.mark.usefixtures("events")
def test_the_audit_log_lists_newest_first(client: Client) -> None:
    page = client.get("/admin/audit/", secure=True).content.decode()
    assert 'aria-current="page">Audit log</a>' in page
    assert page.index("viewer.verified") < page.index("link.created") < page.index("admin.login")
    assert "L-7Q2K" in page and "198.51.100.7" in page


@pytest.mark.usefixtures("events")
def test_filtering_by_kind_and_by_person(client: Client) -> None:
    viewers = client.get("/admin/audit/?kind=viewer", secure=True).content.decode()
    assert "viewer.verified" in viewers and "link.created" not in viewers
    priya = client.get("/admin/audit/?q=priya", secure=True).content.decode()
    assert "link.created" in priya and "admin.login" not in priya


def test_long_logs_are_paged(client: Client) -> None:
    for n in range(120):
        record("admin.login", actor_type=AuditEvent.ActorType.ADMIN, actor=f"a{n}@fernlea.example")
    first = client.get("/admin/audit/", secure=True).content.decode()
    assert "a119@fernlea.example" in first and "a0@fernlea.example" not in first
    assert 'href="?page=2"' in first
    second = client.get("/admin/audit/?page=2", secure=True).content.decode()
    assert "a0@fernlea.example" in second


def test_an_empty_log(client: Client) -> None:
    assert (
        "Nothing recorded yet" in client.get("/admin/audit/?q=nobody", secure=True).content.decode()
    )


@pytest.mark.usefixtures("events")
def test_the_export_is_csv_with_every_field(client: Client) -> None:
    response = client.get("/admin/audit/export.csv?kind=viewer", secure=True)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert response["Content-Disposition"].startswith('attachment; filename="snug-audit-')
    rows = list(csv.reader(io.StringIO(streamed(response).decode())))
    assert rows[0] == [
        "time",
        "actor type",
        "actor",
        "action",
        "target",
        "ip address",
        "user agent",
        "country",
        "details",
    ]
    assert [r[3] for r in rows[1:]] == ["viewer.verified", "viewer.code_refused"]


@pytest.mark.usefixtures("events")
def test_the_export_cannot_carry_a_formula(client: Client) -> None:
    # A viewer can type anything as an address. In a spreadsheet, a cell that
    # starts with = runs as a formula, so every such cell is defused.
    response = client.get("/admin/audit/export.csv", secure=True)
    body = streamed(response).decode()
    assert "'=HYPERLINK" in body
    assert ",=HYPERLINK" not in body


@pytest.mark.usefixtures("events")
def test_exporting_is_itself_audited(client: Client) -> None:
    streamed(client.get("/admin/audit/export.csv?kind=admin", secure=True))
    event = AuditEvent.objects.filter(action="audit.exported").get()
    assert event.actor == "ruth@fernlea.example" and event.details["kind"] == "admin"


def test_the_audit_log_needs_a_signed_in_admin() -> None:
    assert Client().get("/admin/audit/", secure=True).status_code == 302
    assert Client().get("/admin/audit/export.csv", secure=True).status_code == 302
