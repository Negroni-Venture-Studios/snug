"""Uploads show their progress, and every admin page keeps the shared script.

A large PDF takes a while, and a page
that seems to do nothing gets clicked again or abandoned. admin.js sends an
upload form itself, to show a progress bar and disable the button; without
script, the form posts as before.
"""

import time

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.documents.models import Document
from snug.rooms.models import Room

pytestmark = pytest.mark.django_db

ADMIN_JS = "/static/snug/js/admin.js"


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def get(client: Client, path: str) -> str:
    response = client.get(path, secure=True)
    assert response.status_code == 200, path
    return response.content.decode()


def test_both_upload_forms_offer_progress(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    for path in ("/admin/documents/new", f"/admin/documents/{deck.token}"):
        page = get(client, path)
        form = page[page.index('enctype="multipart/form-data"') - 200 :]
        form = form[: form.index("</form>")]
        assert "data-upload" in form, path
        assert '<div class="upload-status" data-upload-status hidden>' in form, path
        assert '<progress max="100" value="0"></progress>' in form, path
        assert ADMIN_JS in page, path


def test_pages_with_their_own_scripts_keep_admin_js(client: Client) -> None:
    # The tabs on a narrow screen, copy buttons and upload progress live there.
    deck = Document.objects.create(title="Seed deck")
    room = Room.objects.create(name="Seed round")
    for path in (
        "/admin/settings/",
        f"/admin/links/for/{deck.token}",
        f"/admin/rooms/{room.token}",
    ):
        assert ADMIN_JS in get(client, path), path


def test_a_scripted_upload_is_told_where_to_go(client: Client, data_folder: object) -> None:
    # A redirect's #section is lost to a script that follows it, and the
    # notice with it, so the script asks for the address instead and goes there.
    from django.core.files.uploadedfile import SimpleUploadedFile

    from tests.pdfs import make_pdf

    pdf = SimpleUploadedFile("deck.pdf", make_pdf((960, 540)), content_type="application/pdf")
    response = client.post(
        "/admin/documents/new",
        {"title": "Seed deck", "pdf": pdf},
        secure=True,
        headers={"X-Snug-Upload": "1"},
    )
    assert response.status_code == 204
    deck = Document.objects.get()
    assert response["Snug-Location"] == f"/admin/documents/{deck.token}#links"
    page = client.get(f"/admin/documents/{deck.token}", secure=True).content.decode()
    assert "Uploaded, 1 page." in page  # the notice waits for that page


def test_without_the_header_it_redirects_as_before(client: Client, data_folder: object) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    from tests.pdfs import make_pdf

    pdf = SimpleUploadedFile("deck.pdf", make_pdf((960, 540)), content_type="application/pdf")
    response = client.post("/admin/documents/new", {"title": "Seed deck", "pdf": pdf}, secure=True)
    assert response.status_code == 302
