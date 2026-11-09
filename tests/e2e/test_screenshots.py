"""The documentation's screenshots, taken from a demo instance in a real browser.

    SNUG_E2E=1 SNUG_SCREENSHOTS=1 \
        uv run pytest tests/e2e/test_screenshots.py --ds=tests.e2e_settings

Builds Fernlea Robotics' data room (tests/demo.py), opens each page in light
and dark, draws a browser window around it (tests/browser_frame.py) and
writes docs/images/screenshots/<name>-light.png and -dark.png, plus the
README's hero. Skipped unless SNUG_SCREENSHOTS is set, since it rewrites
files in the repository.
"""

import io
import os
from pathlib import Path
from typing import Any

import pyotp
import pytest
from django.utils import timezone
from PIL import Image
from playwright.sync_api import Browser, BrowserContext, Page

from snug.accounts import totp
from snug.accounts.models import Admin
from snug.links.models import Link
from tests import browser_frame, demo

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not os.environ.get("SNUG_SCREENSHOTS"), reason="set SNUG_SCREENSHOTS=1"),
]

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "images" / "screenshots"
WIDTH, HEIGHT, SCALE = 1280, 800, 2
EMAIL = "ruth@fernlea.example"
PASSWORD = "correct horse battery staple"
THEMES = ("light", "dark")


def _context(browser: Browser, theme: str, **options: Any) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=SCALE,
        color_scheme=theme,  # type: ignore[arg-type]
        **options,
    )
    if theme == "light":
        context.add_init_script("try { localStorage.setItem('snug-theme', 'light') } catch (e) {}")
    return context


def _shot(page: Page, url: str) -> Image.Image:
    page.goto(url, wait_until="networkidle")
    if "#" in url:  # leave the section's heading clear of the top edge
        page.evaluate("window.scrollBy(0, -40)")
    page.mouse.move(0, 0)
    return Image.open(io.BytesIO(page.screenshot()))


def _write(name: str, theme: str, shot: Image.Image) -> None:
    browser_frame.save(browser_frame.framed(shot, demo.DOMAIN, theme), OUT / f"{name}-{theme}.png")


def _sign_in(browser: Browser, url: str) -> dict[str, Any]:
    """Sign in once and keep the cookies, since a code cannot be used twice."""
    admin = Admin.objects.get(email=EMAIL)
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{url}/admin/login")
    page.get_by_label("Email address").fill(EMAIL)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    page.get_by_label("Six-digit code").fill(pyotp.TOTP(admin.totp_secret).now())
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{url}/admin/")
    state = context.storage_state()
    context.close()
    return state  # type: ignore[return-value]


def _open(page: Page, url: str) -> None:
    """Open a link that needs no code, as an investor would."""
    page.goto(url, wait_until="networkidle")
    page.get_by_role("button", name="Open").click()
    page.wait_for_load_state("networkidle")


def test_take_the_documentation_screenshots(
    live_server: Any, browser: Browser, data_folder: Any
) -> None:
    admin = Admin(
        email=EMAIL, is_active=True, totp_secret=totp.new_secret(), totp_confirmed_at=timezone.now()
    )
    admin.set_password(PASSWORD)
    admin.save()
    built = demo.build(admin)
    url = live_server.url
    # The investor reading the deck opens it without a code, to keep the script short.
    priya = built.links["priya@northfield.example"]
    Link.objects.filter(pk=priya.link.pk).update(require_verification=False)
    signed_in = _sign_in(browser, url)

    admin_pages = {
        "overview": "/admin/",
        "documents": "/admin/documents/",
        "document": f"/admin/documents/{built.deck.token}",
        "workbook-admin": f"/admin/documents/{built.model.token}",
        "upload": "/admin/documents/new",
        "people": "/admin/people/",
        "links": "/admin/links/",
        "link-new": f"/admin/links/for/{built.deck.token}",
        "rooms": "/admin/rooms/",
        "room": f"/admin/rooms/{built.room.token}",
        "audit": "/admin/audit/",
        "settings": "/admin/settings/",
        "settings-email": "/admin/settings/#email",
        "settings-account": "/admin/settings/#account",
    }
    shots: dict[str, dict[str, Image.Image]] = {theme: {} for theme in THEMES}
    # The admin's pages first, so the investors' visits below do not show in them.
    for theme in THEMES:
        context = _context(browser, theme, storage_state=signed_in)
        page = context.new_page()
        for name, path in admin_pages.items():
            shots[theme][name] = _shot(page, f"{url}{path}")
        context.close()
    for theme in THEMES:
        context = _context(browser, theme)
        page = context.new_page()
        taken = shots[theme]
        taken["sign-in"] = _shot(page, f"{url}/admin/login")
        taken["code"] = _shot(page, f"{url}/v/{built.links['sam@kittiwake.example'].token}")
        _open(page, f"{url}/v/{priya.token}")
        taken["reader"] = _shot(page, f"{url}/v/{priya.token}?page=5")
        _open(page, f"{url}/v/{built.model_link.token}")
        taken["workbook"] = Image.open(io.BytesIO(page.screenshot()))
        _open(page, f"{url}/v/{built.nda_link.token}")
        taken["nda"] = Image.open(io.BytesIO(page.screenshot()))
        context.close()

    for theme in THEMES:
        for name, shot in shots[theme].items():
            _write(name, theme, shot)
        browser_frame.save(
            browser_frame.hero(
                shots[theme]["document"], shots[theme]["reader"], demo.DOMAIN, theme
            ),
            OUT / f"hero-{theme}.png",
        )
