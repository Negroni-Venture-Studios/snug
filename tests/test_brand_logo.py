"""The founder's logo: uploaded in Settings, re-encoded in the confined worker.

Before version 1 (DESIGN.md section 4, founder branding). PNG or JPEG only;
the worker decodes it and writes a fresh PNG, at most 800 by 240 pixels, so
a hostile image file never reaches Snug's own process and an SVG, which can
carry script, is never accepted. Investors see it at the top of their pages,
on a light tile in dark mode, and as the favicon.
"""

import io
import time
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent
from snug.core.models import SiteSettings
from snug.documents.models import Document
from snug.links import service
from snug.rendering import sandbox, worker

pytestmark = pytest.mark.django_db


def image(fmt: str, size: tuple[int, int] = (300, 100), mode: str = "RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (31, 95, 139)).save(out, format=fmt)
    return out.getvalue()


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# ---------------------------------------------------------------- the worker


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_png_and_jpeg_become_a_fresh_png(fmt: str) -> None:
    out = worker.logo(image(fmt))
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    assert opened(out).size == (300, 100)


def test_a_large_logo_is_scaled_to_fit() -> None:
    assert opened(worker.logo(image("PNG", (4000, 1000)))).size == (800, 200)
    assert opened(worker.logo(image("PNG", (600, 1200)))).size == (120, 240)


def test_transparency_is_kept() -> None:
    assert opened(worker.logo(image("PNG", mode="RGBA"))).mode == "RGBA"


@pytest.mark.parametrize(
    "data",
    [
        image("GIF"),
        image("WEBP"),
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"not an image at all",
    ],
    ids=["gif", "webp", "svg", "junk"],
)
def test_anything_else_is_refused(data: bytes) -> None:
    with pytest.raises(worker._Refused):
        worker.logo(data)


def test_an_enormous_image_is_refused_before_decoding() -> None:
    with pytest.raises(worker._Refused):
        worker.logo(image("PNG", (worker.MAX_SIDE + 1, 10)))


def test_the_sandbox_runs_it(tmp_path: Path) -> None:
    path = tmp_path / "logo.png"
    path.write_bytes(image("PNG"))
    assert sandbox.logo(path)[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- Settings


@pytest.fixture(autouse=True)
def own_data_folder(data_folder: Path) -> None:
    """Logos are files in the data folder (see conftest.py)."""


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def upload(client: Client, data: bytes, name: str = "logo.png") -> Any:
    file = SimpleUploadedFile(name, data, content_type="image/png")
    return client.post("/admin/settings/logo", {"logo": file}, secure=True)


def test_settings_offers_it(client: Client) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    section = page[page.index('id="branding"') :]
    assert 'action="/admin/settings/logo#branding-logo"' in section
    assert 'accept="image/png,image/jpeg"' in section


def test_uploading_a_logo(client: Client, data_folder: Path) -> None:
    response = upload(client, image("JPEG"), name="Fernlea logo.jpg")
    assert response.status_code == 302
    assert response["Location"] == "/admin/settings/?saved=logo#branding-logo"
    name = SiteSettings.load().logo_name
    assert name.startswith("logo-") and name.endswith(".png")
    stored = data_folder / "branding" / name
    assert stored.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    event = AuditEvent.objects.get(action="settings.logo_changed")
    assert event.details == {"logo": name}


@pytest.mark.parametrize(
    "data", [image("GIF"), b"<svg></svg>", b"junk"], ids=["gif", "svg", "junk"]
)
def test_a_bad_file_is_refused(client: Client, data: bytes) -> None:
    response = upload(client, data)
    assert response.status_code == 400
    assert "Upload a PNG or JPEG image." in response.content.decode()
    assert SiteSettings.load().logo_name == ""


def test_an_oversized_file_is_refused(client: Client) -> None:
    response = upload(client, b"\x89PNG" + b"0" * (2 * 1024 * 1024))
    assert response.status_code == 400
    assert "at most 2 MB" in response.content.decode()


def test_replacing_and_removing_tidy_up(client: Client, data_folder: Path) -> None:
    upload(client, image("PNG"))
    first = SiteSettings.load().logo_name
    upload(client, image("PNG", (200, 200)))
    second = SiteSettings.load().logo_name
    assert first != second
    assert not (data_folder / "branding" / first).exists()
    response = client.post("/admin/settings/logo", {"remove": "1"}, secure=True)
    assert response.status_code == 302
    assert SiteSettings.load().logo_name == ""
    assert not (data_folder / "branding" / second).exists()


# ---------------------------------------------------------------- the viewer


def test_the_logo_is_served(client: Client) -> None:
    upload(client, image("PNG"))
    name = SiteSettings.load().logo_name
    response = Client().get(f"/brand/{name}", secure=True)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert b"".join(response.streaming_content)[:8] == b"\x89PNG\r\n\x1a\n"  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", ["logo-000000000000.png", "../snug.sqlite3", "snug.sqlite3"])
def test_nothing_else_is_served(client: Client, name: str) -> None:
    upload(client, image("PNG"))
    assert Client().get(f"/brand/{name}", secure=True).status_code == 404


def test_investors_see_it(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    issued = service.create(
        document=deck,
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )
    viewer = Client()
    before = viewer.get(f"/v/{issued.token}", secure=True).content.decode()
    assert 'class="founder-logo-image"' not in before
    upload(client, image("PNG"))
    name = SiteSettings.load().logo_name
    page = viewer.get(f"/v/{issued.token}", secure=True).content.decode()
    assert f'<img class="founder-logo-image" src="/brand/{name}"' in page
    # This logo is wide, so the tab icon is not it (see test_brand_icon.py).


def test_the_name_sits_beside_the_logo(client: Client) -> None:
    deck = Document.objects.create(title="Seed deck")
    issued = service.create(
        document=deck,
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )
    upload(client, image("PNG"))
    client.post(
        "/admin/settings/branding", {"company": "Fernlea Robotics", "show_name": "1"}, secure=True
    )
    name = SiteSettings.load().logo_name
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    # The name follows as text, so the image itself needs no alt text.
    assert (
        f'<img class="founder-logo-image" src="/brand/{name}" alt="">'
        '<span class="founder-name">Fernlea Robotics</span>'
    ) in page


def test_the_name_can_be_left_off_beside_the_logo(client: Client) -> None:
    """For a logo that already spells out the name."""
    page = client.get("/admin/settings/", secure=True).content.decode()
    form = page[page.index('action="/admin/settings/branding') :]
    form = form[: form.index("</form>")]
    assert 'name="show_name" type="checkbox" value="1" checked' in form

    deck = Document.objects.create(title="Seed deck")
    issued = service.create(
        document=deck,
        email="priya@northfield.example",
        note="",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=None,
    )
    upload(client, image("PNG"))
    client.post("/admin/settings/branding", {"company": "Fernlea Robotics"}, secure=True)
    assert SiteSettings.load().show_name_with_logo is False
    event = AuditEvent.objects.filter(action="settings.branding_changed").latest("pk")
    assert event.details == {"company": "Fernlea Robotics", "show_name": False}
    viewer = Client()
    page = viewer.get(f"/v/{issued.token}", secure=True).content.decode()
    assert 'class="founder-name"' not in page
    assert '<img class="founder-logo-image"' in page

    # Without a logo the name always shows: there is nothing else.
    client.post("/admin/settings/logo", {"remove": "1"}, secure=True)
    page = viewer.get(f"/v/{issued.token}", secure=True).content.decode()
    assert '<span class="founder-name">Fernlea Robotics</span>' in page
