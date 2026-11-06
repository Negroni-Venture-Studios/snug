"""Admin pages in a real browser: what only a click can prove under the CSP."""

from typing import Any

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.django_db(transaction=True)]


def test_the_eye_shows_and_hides_the_password(live_server: Any, page: Page) -> None:
    page.goto(f"{live_server.url}/admin/login")
    field = page.get_by_label("Password", exact=True)
    eye = page.get_by_role("button", name="Show password")
    expect(eye).to_be_visible()  # the script ran, so the button appeared
    field.fill("correct horse battery staple")
    expect(field).to_have_attribute("type", "password")

    eye.click()
    expect(field).to_have_attribute("type", "text")
    hide = page.get_by_role("button", name="Hide password")
    expect(hide).to_have_attribute("aria-pressed", "true")
    expect(field).to_be_focused()

    hide.click()
    expect(field).to_have_attribute("type", "password")
    expect(page.get_by_role("button", name="Show password")).to_have_attribute(
        "aria-pressed", "false"
    )
