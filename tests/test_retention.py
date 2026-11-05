"""Reading data is deleted some months after its link ends.

Decided 5 November 2026: 12 months after a link is revoked or expires, by
default, set in Settings (some founders, in healthcare say, need 7 years).
"""

import time
from datetime import datetime, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.analytics import retention
from snug.analytics.models import PageView, Visit
from snug.audit.models import AuditEvent
from snug.core.models import SiteSettings
from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link

pytestmark = pytest.mark.django_db

NOW = timezone.make_aware(datetime(2027, 11, 5, 12, 0))


@pytest.fixture
def document() -> Document:
    return Document.objects.create(title="Seed deck")


def link_with_reading(document: Document, **ended: datetime) -> Link:
    link = service.create(
        document=document,
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    ).link
    Link.objects.filter(pk=link.pk).update(**ended)
    visit = Visit.objects.create(
        token=f"v{link.pk}",
        link=link,
        email=link.email,
        document=document,
        version_number=1,
        started_at=NOW - timedelta(days=500),
        last_seen_at=NOW - timedelta(days=500),
        current_page=1,
        clock_at=NOW - timedelta(days=500),
    )
    PageView.objects.create(visit=visit, page=1, seconds=30, first_seen_at=visit.started_at)
    return link


def test_reading_data_goes_twelve_months_after_the_link_ends(document: Document) -> None:
    old = link_with_reading(document, revoked_at=NOW - timedelta(days=366))
    recent = link_with_reading(document, revoked_at=NOW - timedelta(days=300))
    expired = link_with_reading(document, expires_at=NOW - timedelta(days=400))
    active = link_with_reading(document)  # visits long ago, but the link still works
    assert retention.purge(now=NOW) == 2
    assert set(Visit.objects.values_list("link", flat=True)) == {recent.pk, active.pk}
    assert not PageView.objects.filter(visit__link__in=[old, expired]).exists()
    # The links themselves, and the audit log, stay.
    assert Link.objects.count() == 4


def test_the_period_follows_the_setting(document: Document) -> None:
    link_with_reading(document, revoked_at=NOW - timedelta(days=400))
    settings = SiteSettings.load()
    settings.reading_retention_months = 84  # seven years
    settings.save()
    assert retention.purge(now=NOW) == 0
    assert Visit.objects.count() == 1


def test_housekeeping_runs_the_purge_and_says_what_it_did(
    document: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("django.utils.timezone.now", lambda: NOW)
    link_with_reading(document, revoked_at=NOW - timedelta(days=400))
    out = StringIO()
    call_command("housekeeping", stdout=out)
    assert "reading data: 1 visit" in out.getvalue()
    assert not Visit.objects.exists()
    assert "priya" not in out.getvalue()  # counts only, never who
    assert AuditEvent.objects.filter(action="system.reading_data_deleted").get().details == {
        "visits": 1,
        "months": 12,
    }


def test_housekeeping_clears_other_expired_things(document: Document) -> None:
    from snug.accounts.models import AdminSetup
    from snug.viewer.models import VerificationCode, ViewerSession

    admin = Admin.objects.create(email="jo@fernlea.example")
    link = link_with_reading(document)
    past = timezone.now() - timedelta(days=1)
    VerificationCode.objects.create(link=link, code_hash="x", expires_at=past)
    ViewerSession.objects.create(
        token_hash="y",
        link=link,
        link_token_hash=link.token_hash,
        email=link.email,
        verified=True,
        expires_at=past,
    )
    AdminSetup.objects.create(admin=admin, purpose="invite", token_hash="z", expires_at=past)
    call_command("housekeeping", stdout=StringIO())
    assert not VerificationCode.objects.exists()
    assert not ViewerSession.objects.exists()
    assert not AdminSetup.objects.exists()
    assert Visit.objects.exists()  # an active link's reading data stays


# ---------------------------------------------------------------- the setting


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def test_the_period_is_set_in_settings(client: Client) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    assert 'name="months" type="number" min="1" max="120" value="12"' in page
    response = client.post("/admin/settings/retention", {"months": "84"}, secure=True)
    assert response.status_code == 302
    assert SiteSettings.load().reading_retention_months == 84
    event = AuditEvent.objects.get(action="settings.retention_changed")
    assert event.details == {"months": 84}


@pytest.mark.parametrize("months", ["0", "121", "a year", "", "-3"])
def test_the_period_must_be_sensible(client: Client, months: str) -> None:
    response = client.post("/admin/settings/retention", {"months": months}, secure=True)
    assert response.status_code == 400
    assert "from 1 to 120" in response.content.decode()
    assert SiteSettings.load().reading_retention_months == 12


def test_the_server_runs_housekeeping_every_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    from snug import gunicorn_conf

    started: list[dict[str, object]] = []

    class FakeThread:
        def __init__(self, **kwargs: object) -> None:
            started.append(kwargs)

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr("snug.gunicorn_conf.threading.Thread", FakeThread)
    gunicorn_conf.when_ready(server=None)
    assert started == [
        {
            "target": gunicorn_conf._housekeeping,
            "name": "housekeeping",
            "daemon": True,
            "started": True,
        }
    ]
    assert gunicorn_conf.HOUSEKEEPING_EVERY == 3600


def test_a_failed_run_never_stops_housekeeping(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import subprocess as sp

    from snug import gunicorn_conf

    calls: list[int] = []

    def run(*args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise sp.TimeoutExpired(cmd="snug housekeeping", timeout=600)
        if len(calls) == 2:
            return sp.CompletedProcess(args=[], returncode=1)
        raise SystemExit  # end the test's loop

    monkeypatch.setattr("snug.gunicorn_conf.subprocess.run", run)
    monkeypatch.setattr("snug.gunicorn_conf.time.sleep", lambda seconds: None)
    with pytest.raises(SystemExit):
        gunicorn_conf._housekeeping()
    assert len(calls) == 3  # it kept going after a timeout and a failure
    assert "Housekeeping failed: TimeoutExpired" in caplog.text
    assert "Housekeeping failed: exit status 1" in caplog.text


def test_saving_the_period_says_so_beside_it(client: Client) -> None:
    page = client.post(
        "/admin/settings/retention", {"months": "24"}, secure=True, follow=True
    ).content.decode()
    form = page[page.index('action="/admin/settings/retention"') :]
    assert "Saved. Reading data is kept for 24 months" in form
