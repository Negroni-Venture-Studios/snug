"""The icon in investors' browser tabs.

A browser draws a tab's icon in a small square, so a wide logo makes an
unreadable sliver. The founder can upload a square icon of its own, redrawn
by the confined worker like the logo. Without one, a roughly square logo
serves; failing that, a tile with the company's initial in the accent colour.
Investors' pages never show Snug's own icon.
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
from snug.core import brand
from snug.core.models import SiteSettings
from snug.documents.models import Document
from snug.links import service
from snug.rendering import sandbox, worker

pytestmark = pytest.mark.django_db


def image(size: tuple[int, int], fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (31, 95, 139)).save(out, format=fmt)
    return out.getvalue()


def opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# ---------------------------------------------------------------- the worker


@pytest.mark.parametrize("size", [(64, 64), (2000, 2000), (300, 100)])
def test_an_icon_is_always_a_square_png(size: tuple[int, int]) -> None:
    out = worker.icon(image(size))
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    assert opened(out).size == (worker.ICON_SIDE, worker.ICON_SIDE)


def test_a_wide_image_is_centred_on_a_clear_square() -> None:
    drawn = opened(worker.icon(image((300, 100)))).convert("RGBA")
    corner: Any = drawn.getpixel((0, 0))
    assert corner[3] == 0  # clear above and below
    assert drawn.getpixel((90, 90)) == (31, 95, 139, 255)


@pytest.mark.parametrize("data", [image((64, 64), "GIF"), b"<svg></svg>", b"junk"])
def test_anything_else_is_refused(data: bytes) -> None:
    with pytest.raises(worker._Refused):
        worker.icon(data)


def test_the_sandbox_runs_it(tmp_path: Path) -> None:
    path = tmp_path / "icon.png"
    path.write_bytes(image((64, 64)))
    assert sandbox.icon(path)[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- Settings


@pytest.fixture(autouse=True)
def own_data_folder(data_folder: Path) -> None:
    """Icons are files in the data folder (see conftest.py)."""


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def upload(client: Client, kind: str, data: bytes) -> Any:
    file = SimpleUploadedFile(f"{kind}.png", data, content_type="image/png")
    return client.post(f"/admin/settings/{kind}", {kind: file}, secure=True)


def test_settings_offers_it(client: Client) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    section = page[page.index('id="branding"') :]
    assert 'action="/admin/settings/icon#branding-icon"' in section
    assert 'name="icon" type="file" accept="image/png,image/jpeg"' in section


def test_uploading_and_removing_an_icon(client: Client, data_folder: Path) -> None:
    response = upload(client, "icon", image((128, 128)))
    assert response["Location"] == "/admin/settings/?saved=icon#branding-icon"
    name = SiteSettings.load().icon_name
    assert name.startswith("icon-") and name.endswith(".png")
    assert (data_folder / "branding" / name).exists()
    event = AuditEvent.objects.get(action="settings.icon_changed")
    assert event.details == {"icon": name}
    served: Any = Client().get(f"/brand/{name}", secure=True)
    assert served.status_code == 200 and served["Content-Type"] == "image/png"
    assert b"".join(served.streaming_content)[:8] == b"\x89PNG\r\n\x1a\n"
    # Close the file without response.close(), whose signal closes the test database.
    for close in served._resource_closers:
        close()

    client.post("/admin/settings/icon", {"remove": "1"}, secure=True)
    assert SiteSettings.load().icon_name == ""
    assert not (data_folder / "branding" / name).exists()
    assert Client().get(f"/brand/{name}", secure=True).status_code == 404


def test_a_bad_icon_is_refused(client: Client) -> None:
    response = upload(client, "icon", b"<svg></svg>")
    assert response.status_code == 400
    assert "Upload a PNG or JPEG image." in response.content.decode()


# ---------------------------------------------------------------- which icon investors get


def favicon(client: Client) -> str:
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
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    start = page.index('<link rel="icon"')
    return page[start : page.index(">", start) + 1]


def test_without_anything_it_is_the_initial(client: Client) -> None:
    client.post(
        "/admin/settings/branding", {"company": "Fernlea Robotics", "show_name": "1"}, secure=True
    )
    link = favicon(client)
    assert 'href="/brand/initial.svg?v=' in link and 'type="image/svg+xml"' in link
    assert "snug/img/favicon" not in link  # never Snug's own


def test_a_square_logo_serves(client: Client) -> None:
    upload(client, "logo", image((200, 220)))
    assert favicon(client) == (
        f'<link rel="icon" href="/brand/{SiteSettings.load().logo_name}" type="image/png">'
    )


def test_a_wide_logo_does_not(client: Client) -> None:
    upload(client, "logo", image((600, 200)))
    assert "/brand/initial.svg" in favicon(client)


def test_an_uploaded_icon_comes_first(client: Client) -> None:
    upload(client, "logo", image((200, 200)))
    upload(client, "icon", image((64, 64)))
    assert favicon(client) == (
        f'<link rel="icon" href="/brand/{SiteSettings.load().icon_name}" type="image/png">'
    )


# ---------------------------------------------------------------- the initial


def test_the_initial_tile(client: Client) -> None:
    client.post(
        "/admin/settings/branding",
        {"company": "fernlea Robotics", "accent": "#ffd400", "show_name": "1"},
        secure=True,
    )
    response = Client().get("/brand/initial.svg", secure=True)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    svg = response.content.decode()
    shades = brand.palette("#ffd400")
    assert f'fill="{shades.fill_light}"' in svg
    assert f'fill="{shades.on_light}"' in svg
    assert ">F</text>" in svg


def test_without_a_name_the_tile_is_plain(client: Client) -> None:
    svg = Client().get("/brand/initial.svg", secure=True).content.decode()
    assert "<rect" in svg and "<text" not in svg


def test_the_initial_is_a_letter_or_digit_only(client: Client) -> None:
    client.post(
        "/admin/settings/branding",
        {"company": '<"&> 3D Labs', "show_name": "1"},
        secure=True,
    )
    svg = Client().get("/brand/initial.svg", secure=True).content.decode()
    assert ">3</text>" in svg and "&" not in svg.replace("&amp;", "")
