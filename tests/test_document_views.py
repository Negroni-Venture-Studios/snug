"""The admin's document pages: listing, uploading, versions and previews."""

import time
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
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


@pytest.fixture
def client(admin: Admin) -> Client:
    browser = Client(REMOTE_ADDR="198.51.100.7")
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


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
    assert "This file is not a PDF" in response.content.decode()
    assert not Document.objects.exists()
    assert actions() == ["document.upload_refused"]
    assert list((data_folder / "originals").iterdir()) == []


def test_oversized_uploads_are_refused_before_they_are_read(admin: Admin) -> None:
    # No CSRF token, and a declared size over the limit: refused from the
    # header alone, before Django reads the body for its CSRF check.
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    response = strict.post(
        "/admin/documents/new",
        {"title": "Huge"},
        secure=True,
        CONTENT_LENGTH=str(500 * 1024 * 1024),
    )
    assert response.status_code == 413
    assert "This file is too large" in response.content.decode()
    assert not Document.objects.exists()


def test_uploads_still_need_a_csrf_token(admin: Admin) -> None:
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(admin)
    response = strict.post("/admin/documents/new", {"title": "Deck", "pdf": pdf()}, secure=True)
    assert response.status_code == 403
    assert not Document.objects.exists()


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
