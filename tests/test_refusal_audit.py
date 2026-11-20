"""A refused request adds at most one audit row in a limit's window.

Security audit, 19 November 2026 (F7, #195): every request a rate limit
refused, and every UK-only page opened from abroad, appended an audit row.
Audit rows are kept for good and backed up, so one client could grow the
database and its backups at request rate. A refusal is now audited the
first time in a window for both the address and the account or link, so
it is still on record, and a flood adds one row.
"""

from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from snug.accounts import limits as admin_limits
from snug.accounts import totp
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core import geoip, ratelimit
from snug.documents.models import Document
from snug.links import service
from snug.viewer import limits as viewer_limits

pytestmark = pytest.mark.django_db

EMAIL = "ruth@fernlea.example"
PASSWORD = "correct horse battery staple"
INVESTOR = "priya@northfield.example"
IP = "198.51.100.7"
MORE = 5


def post(client: Client, path: str, data: dict[str, str]) -> Any:
    return client.post(path, data, secure=True)


def rows(action: str, reason: str | None = None) -> int:
    found = AuditEvent.objects.filter(action=action)
    if reason is not None:
        found = found.filter(details__reason=reason)
    return found.count()


def fill(limit: ratelimit.Limit, identity: str) -> None:
    for _ in range(limit.limit):
        ratelimit.hit(limit, identity)


@pytest.fixture
def admin() -> Admin:
    account = Admin(
        email=EMAIL, is_active=True, totp_secret=totp.new_secret(), totp_confirmed_at=timezone.now()
    )
    account.set_password(PASSWORD)
    account.save()
    return account


def link(**options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "note": "",
        "expires_at": None,
        "require_verification": True,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(
        document=Document.objects.create(title="Seed deck"), email=INVESTOR, **fields
    )


@pytest.mark.usefixtures("admin")
def test_refused_sign_ins_are_audited_once() -> None:
    fill(admin_limits.PASSWORD_PER_IP, IP)
    client = Client(REMOTE_ADDR=IP)
    for _ in range(MORE):
        response = post(client, "/admin/login", {"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 429
    assert rows("admin.login_failed", "rate_limited") == 1


def test_refused_sign_in_codes_are_audited_once(admin: Admin) -> None:
    client = Client(REMOTE_ADDR=IP)
    assert post(client, "/admin/login", {"email": EMAIL, "password": PASSWORD}).status_code == 302
    fill(admin_limits.CODE_PER_IP, IP)
    for _ in range(MORE):
        assert post(client, "/admin/login/code", {"code": "000000"}).status_code == 429
    assert rows("admin.login_failed", "rate_limited") == 1


@pytest.mark.usefixtures("email_on")
def test_refused_code_requests_are_audited_once() -> None:
    issued = link()
    fill(viewer_limits.SEND_PER_IP, IP)
    client = Client(REMOTE_ADDR=IP)
    for _ in range(MORE):
        response = post(client, f"/v/{issued.token}/code/send", {"email": INVESTOR})
        assert response.status_code == 429
    assert rows("viewer.code_refused", "rate_limited") == 1


@pytest.mark.usefixtures("email_on")
def test_refused_code_checks_are_audited_once() -> None:
    issued = link()
    fill(viewer_limits.CHECK_PER_IP, IP)
    client = Client(REMOTE_ADDR=IP)
    for _ in range(MORE):
        assert post(client, f"/v/{issued.token}/code", {"code": "000000"}).status_code == 429
    assert rows("viewer.code_wrong", "rate_limited") == 1


@pytest.mark.usefixtures("email_on")
def test_neutral_refusals_are_audited_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("snug.core.mail.in_background", lambda task: task())
    issued = link()
    fill(viewer_limits.WRONG_EMAIL_PER_LINK, str(issued.link.pk))
    fill(viewer_limits.SEND_PER_LINK, str(issued.link.pk))
    client = Client(REMOTE_ADDR=IP)
    for _ in range(MORE):
        response = post(client, f"/v/{issued.token}/code/send", {"email": INVESTOR})
        assert response.status_code == 200  # the neutral reply
    assert rows("viewer.code_refused", "rate_limited") == 1


def test_uk_only_refusals_are_audited_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoip, "country", lambda address: "FR")
    issued = link(uk_only=True)
    client = Client(REMOTE_ADDR=IP)
    for _ in range(MORE + 1):
        assert client.get(f"/v/{issued.token}", secure=True).status_code == 403
    assert rows("viewer.outside_uk") == 1


def test_a_refusal_is_audited_again_from_another_address_and_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Once per window for each address and each link: a second visitor's
    # refusal on another link is still on record.
    monkeypatch.setattr(geoip, "country", lambda address: "FR")
    first, second = link(uk_only=True), link(uk_only=True)
    Client(REMOTE_ADDR=IP).get(f"/v/{first.token}", secure=True)
    Client(REMOTE_ADDR="203.0.113.50").get(f"/v/{second.token}", secure=True)
    assert rows("viewer.outside_uk") == 2
