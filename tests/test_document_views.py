"""The admin's document pages: listing, uploading, versions and previews."""

import time
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.documents.models import Document, DocumentVersion
from tests.pdfs import make_pdf

pytestmark = pytest.mark.django_db

DECK = make_pdf((960, 540), (960, 540))
EMAIL = "ruth@fernlea.example"


@pytest.fixture(autouse=True)
def own_data_folder(data_folder: Path) -> None:
    """Every test here gets its own data folder (see conftest.py)."""


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(email=EMAIL, is_active=True)


def sign_in(browser: Client, admin: Admin) -> Client:
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.fixture
def client(admin: Admin) -> Client:
    return sign_in(Client(REMOTE_ADDR="198.51.100.7"), admin)


def pdf(data: bytes = DECK, name: str = "Seed deck v3.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def upload_new(client: Client, title: str = "Seed deck", data: bytes = DECK) -> Any:
    return client.post("/admin/documents/new", {"title": title, "pdf": pdf(data)}, secure=True)


def actions() -> list[str]:
    return list(AuditEvent.objects.order_by("id").values_list("action", flat=True))


# ---------------------------------------------------------------- access


@pytest.mark.parametrize(
    "path", ["/admin/documents/", "/admin/documents/new", "/admin/documents/abc"]
)
def test_document_pages_need_a_signed_in_admin(path: str) -> None:
    response = Client().get(path, secure=True)
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login?next=")


def test_an_unknown_document_is_not_found(client: Client) -> None:
    assert client.get("/admin/documents/no-such-token", secure=True).status_code == 404


# ---------------------------------------------------------------- listing


def test_an_empty_list_invites_an_upload(client: Client) -> None:
    page = client.get("/admin/documents/", secure=True).content.decode()
    assert "No documents yet" in page
    assert 'href="/admin/documents/new"' in page
    assert 'aria-current="page">Documents</a>' in page


def test_documents_are_listed_with_their_latest_version(client: Client) -> None:
    upload_new(client)
    page = client.get("/admin/documents/", secure=True).content.decode()
    assert "Seed deck" in page
    assert "Version 1" in page
    assert "2 pages" in page


# ---------------------------------------------------------------- uploading a new document


def test_uploading_creates_a_document_and_version_one(client: Client) -> None:
    response = upload_new(client)
    document = Document.objects.get()
    assert response.status_code == 302
    assert response["Location"] == f"/admin/documents/{document.token}#links"
    version = document.latest_version()
    assert version is not None and version.number == 1 and version.page_count == 2
    assert version.uploaded_by is not None and version.uploaded_by.email == EMAIL
    assert version.original_filename == "Seed deck v3.pdf"
    assert actions() == ["document.created", "document.version_uploaded"]
    event = AuditEvent.objects.get(action="document.version_uploaded")
    assert (event.actor, event.ip_address) == (EMAIL, "198.51.100.7")
    assert event.details["pages"] == 2


def test_titles_are_tidied(client: Client) -> None:
    upload_new(client, title="  Seed \n  deck  " + "x" * 300)
    title = Document.objects.get().title
    assert title.startswith("Seed deck x")
    assert len(title) == 200


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"title": "", "pdf": "file"}, "Give the document a title."),
        ({"title": "Deck"}, "Choose a PDF"),
    ],
)
def test_title_and_file_are_required(client: Client, data: dict[str, str], message: str) -> None:
    if data.get("pdf"):
        data = {**data, "pdf": pdf()}  # type: ignore[dict-item]
    response = client.post("/admin/documents/new", data, secure=True)
    assert response.status_code == 400
    assert message in response.content.decode()
    assert not Document.objects.exists()


def test_a_refused_file_creates_nothing(client: Client, data_folder: Path) -> None:
    response = upload_new(client, data=b"Not a PDF at all")
    assert response.status_code == 400
    assert "Snug takes PDFs, Word, PowerPoint and Excel files" in response.content.decode()
    assert not Document.objects.exists()
    assert actions() == ["document.upload_refused"]
    assert list((data_folder / "originals").iterdir()) == []


@pytest.mark.parametrize("first", [True, False], ids=["new document", "new version"])
def test_the_file_is_checked_before_the_database_is_locked(
    client: Client, monkeypatch: pytest.MonkeyPatch, first: bool
) -> None:
    # SQLite runs write transactions in IMMEDIATE mode: one held open while the
    # worker reads the file would keep every other writer waiting until it ends.
    from django.db import connection

    from snug.rendering import sandbox

    if not first:
        upload_new(client)
    # The test itself runs in a transaction: count only the blocks the view opens.
    outside = len(connection.atomic_blocks)
    opened: list[int] = []
    original = sandbox.inspect

    def inspect(path: Any) -> dict[str, Any]:
        opened.append(len(connection.atomic_blocks) - outside)
        return original(path)

    monkeypatch.setattr(sandbox, "inspect", inspect)
    if first:
        response = upload_new(client)
    else:
        document = Document.objects.get()
        response = client.post(
            f"/admin/documents/{document.token}/versions", {"pdf": pdf()}, secure=True
        )
    assert response.status_code == 302
    assert opened == [0]
    assert DocumentVersion.objects.count() == (1 if first else 2)


def test_a_file_the_worker_refuses_creates_nothing(
    client: Client, monkeypatch: pytest.MonkeyPatch, data_folder: Path
) -> None:
    # A PDF by its first bytes, refused only once the worker has read it.
    from snug.rendering import sandbox

    def refuse(path: Any) -> dict[str, Any]:
        raise sandbox.WorkerError("password")

    monkeypatch.setattr(sandbox, "inspect", refuse)
    response = upload_new(client)
    assert response.status_code == 400
    assert "This PDF is protected by a password." in response.content.decode()
    assert not Document.objects.exists()
    assert actions() == ["document.upload_refused"]
    assert list((data_folder / "originals").iterdir()) == []
    assert list((data_folder / "tmp").iterdir()) == []


HOSTILE = '<img src=x onerror="alert(1)">'
ESCAPED = "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"


def test_a_refused_upload_page_escapes_the_title_and_file_name(client: Client) -> None:
    # admin.js puts this page in place of the form, so nothing in it may be markup.
    response = client.post(
        "/admin/documents/new",
        {"title": HOSTILE, "pdf": pdf(b"Not a PDF at all", name=f"{HOSTILE}.pdf")},
        secure=True,
    )
    page = response.content.decode()
    assert response.status_code == 400
    assert "<img src=x" not in page
    assert f'value="{ESCAPED}"' in page


def test_a_refused_version_escapes_the_title_in_its_error(client: Client) -> None:
    upload_new(client, title=HOSTILE)
    document = Document.objects.get()
    workbook = SimpleUploadedFile(f"{HOSTILE}.xlsx", b"PK\x03\x04" + b"\0" * 64)
    response = client.post(
        f"/admin/documents/{document.token}/versions", {"pdf": workbook}, secure=True
    )
    page = response.content.decode()
    assert response.status_code == 400
    assert "<img src=x" not in page
    assert f"{ESCAPED} is a PDF. Upload a PDF as its new version" in page


def test_oversized_uploads_are_refused_before_they_are_read(admin: Admin) -> None:
    # No CSRF token, and a declared size over the limit: refused from the
    # header alone, before Django reads the body for its CSRF check.
    strict = Client(enforce_csrf_checks=True)
    sign_in(strict, admin)
    response = strict.post(
        "/admin/documents/new",
        {"title": "Huge"},
        secure=True,
        # Over the larger of the two limits: documents' and videos'.
        CONTENT_LENGTH=str(5000 * 1024 * 1024),
    )
    assert response.status_code == 413
    assert "This file is too large" in response.content.decode()
    assert not Document.objects.exists()


def test_uploads_still_need_a_csrf_token(admin: Admin) -> None:
    strict = Client(enforce_csrf_checks=True)
    sign_in(strict, admin)
    response = strict.post("/admin/documents/new", {"title": "Deck", "pdf": pdf()}, secure=True)
    assert response.status_code == 403
    assert not Document.objects.exists()


# Anyone but a signed-in admin gets only the allowance for a form's fields, and
# is refused from the declared size before Django's CSRF check reads the body.
# Under the documents' limit, so only the missing admin can refuse it.
BIG = str(50 * 1024 * 1024)


@pytest.fixture
def bodies_read(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The paths whose request bodies Django parsed."""
    read: list[str] = []
    original = HttpRequest._load_post_and_files

    def load(request: HttpRequest) -> None:
        read.append(request.path)
        original(request)

    monkeypatch.setattr(HttpRequest, "_load_post_and_files", load)
    return read


def browser_with_a_csrf_cookie() -> Client:
    browser = Client(enforce_csrf_checks=True, REMOTE_ADDR="198.51.100.7")
    browser.get("/admin/login", secure=True)
    assert settings.CSRF_COOKIE_NAME in browser.cookies
    return browser


def post_big(browser: Client, path: str) -> Any:
    return browser.post(
        path,
        {"title": "Huge"},
        secure=True,
        headers={"Origin": "https://snug.example.com"},
        CONTENT_LENGTH=BIG,
    )


@pytest.mark.parametrize("path", ["/admin/documents/new", "/admin/documents/abc/versions"])
def test_an_upload_from_no_one_is_refused_unread(path: str, bodies_read: list[str]) -> None:
    response = post_big(browser_with_a_csrf_cookie(), path)
    assert response.status_code == 302
    assert response["Location"] == f"/admin/login?next={path}"
    assert bodies_read == []


def test_an_upload_part_way_through_signing_in_is_refused_unread(
    bodies_read: list[str],
) -> None:
    browser = browser_with_a_csrf_cookie()
    session = browser.session
    session[sessions.PENDING] = {"admin_id": 1, "at": time.time(), "failures": 0, "next": "/"}
    session.save()
    response = post_big(browser, "/admin/documents/new")
    assert response.status_code == 302
    assert bodies_read == []


def test_an_upload_on_an_expired_session_is_refused_unread(
    admin: Admin, bodies_read: list[str]
) -> None:
    browser = browser_with_a_csrf_cookie()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time() - 13 * 3600
    session.save()
    response = post_big(browser, "/admin/documents/new")
    assert response["Location"] == "/admin/login?next=/admin/documents/new"
    assert bodies_read == []


def test_a_large_post_to_a_viewer_link_is_refused_unread(bodies_read: list[str]) -> None:
    response = post_big(browser_with_a_csrf_cookie(), "/v/abc/code")
    assert response.status_code == 413
    assert bodies_read == []


def test_a_signed_in_admin_still_gets_the_full_allowance(
    client: Client, bodies_read: list[str]
) -> None:
    assert upload_new(client).status_code == 302
    assert bodies_read == ["/admin/documents/new"]


# ---------------------------------------------------------------- the document page and versions


def test_the_document_page_shows_versions_and_a_preview(client: Client) -> None:
    upload_new(client)
    document = Document.objects.get()
    page = client.get(f"/admin/documents/{document.token}", secure=True).content.decode()
    assert f'src="/admin/documents/{document.token}/v1/page-1.webp"' in page
    assert 'width="800" height="450"' in page
    assert "Seed deck v3.pdf" in page


def test_a_new_version_is_added(client: Client) -> None:
    upload_new(client)
    document = Document.objects.get()
    response = client.post(
        f"/admin/documents/{document.token}/versions",
        {"pdf": pdf(make_pdf((595, 842)), "Seed deck v4.pdf")},
        secure=True,
    )
    assert response.status_code == 302
    latest = document.latest_version()
    assert latest is not None and latest.number == 2 and latest.page_count == 1
    assert actions()[-1] == "document.version_uploaded"


def test_a_refused_new_version_keeps_the_old_one(client: Client) -> None:
    upload_new(client)
    document = Document.objects.get()
    response = client.post(
        f"/admin/documents/{document.token}/versions",
        {"pdf": pdf(b"%PDF-1.7 garbage")},
        secure=True,
    )
    assert response.status_code == 400
    assert "could not be read" in response.content.decode()
    assert DocumentVersion.objects.count() == 1


def test_a_new_version_needs_a_file_and_a_post(client: Client) -> None:
    upload_new(client)
    token = Document.objects.get().token
    assert client.post(f"/admin/documents/{token}/versions", {}, secure=True).status_code == 400
    assert client.get(f"/admin/documents/{token}/versions", secure=True).status_code == 405


# ---------------------------------------------------------------- previews


def test_previews_are_webp_for_admins(client: Client) -> None:
    upload_new(client)
    token = Document.objects.get().token
    response = client.get(f"/admin/documents/{token}/v1/page-1.webp", secure=True)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert response["Cache-Control"] == "private, max-age=3600"
    assert b"".join(response.streaming_content)[:4] == b"RIFF"  # type: ignore[attr-defined]


@pytest.mark.parametrize("path", ["v1/page-3.webp", "v1/page-0.webp", "v2/page-1.webp"])
def test_previews_outside_the_document_are_not_found(client: Client, path: str) -> None:
    upload_new(client)
    token = Document.objects.get().token
    assert client.get(f"/admin/documents/{token}/{path}", secure=True).status_code == 404


def test_previews_of_another_document_are_not_found(client: Client) -> None:
    upload_new(client, title="First")
    upload_new(client, title="Second")
    first, second = Document.objects.order_by("id")
    response = client.get(f"/admin/documents/{first.token}/v1/page-1.webp", secure=True)
    assert response.status_code == 200
    b"".join(response.streaming_content)  # type: ignore[attr-defined]  # reading closes the file
    # The version number belongs to the document in the URL, and no other.
    second.versions.all().delete()
    assert (
        client.get(f"/admin/documents/{second.token}/v1/page-1.webp", secure=True).status_code
        == 404
    )


def test_previews_need_a_signed_in_admin(client: Client) -> None:
    upload_new(client)
    token = Document.objects.get().token
    assert Client().get(f"/admin/documents/{token}/v1/page-1.webp", secure=True).status_code == 302


# ---------------------------------------------------------------- renaming
# Asked for 13 November 2026: a typo in a title ("Ladded") could not be fixed.


def made(client: Client) -> Document:
    assert upload_new(client, title="Cambridge Startup Ladded").status_code == 302
    return Document.objects.get()


def rename(client: Client, document: Document, title: str) -> Any:
    return client.post(f"/admin/documents/{document.token}/rename", {"title": title}, secure=True)


def test_a_document_can_be_renamed(client: Client) -> None:
    document = made(client)
    response = rename(client, document, "  Cambridge   Startup Ladder ")
    assert response.status_code == 302
    assert response["Location"].endswith("#rename")
    document.refresh_from_db()
    assert document.title == "Cambridge Startup Ladder"
    assert "document.renamed" in actions()
    page = client.get(response["Location"], secure=True).content.decode()
    assert "Renamed." in page


def test_the_document_page_offers_renaming(client: Client) -> None:
    document = made(client)
    page = client.get(f"/admin/documents/{document.token}", secure=True).content.decode()
    assert 'id="rename"' in page
    assert 'value="Cambridge Startup Ladded"' in page


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_a_title_must_be_given_and_not_too_long(client: Client, title: str) -> None:
    document = made(client)
    response = rename(client, document, title)
    assert response.status_code == 400
    assert "Give the document a title, of 200 characters or fewer." in response.content.decode()
    document.refresh_from_db()
    assert document.title == "Cambridge Startup Ladded"
    assert "document.renamed" not in actions()


def test_renaming_to_the_same_title_records_nothing(client: Client) -> None:
    document = made(client)
    assert rename(client, document, "Cambridge Startup Ladded").status_code == 302
    assert "document.renamed" not in actions()


def test_renaming_needs_a_signed_in_admin(client: Client) -> None:
    document = made(client)
    response = Client().post(
        f"/admin/documents/{document.token}/rename", {"title": "Hijacked"}, secure=True
    )
    assert response.status_code in (302, 403)
    document.refresh_from_db()
    assert document.title == "Cambridge Startup Ladded"
