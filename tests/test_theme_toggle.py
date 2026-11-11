"""The theme toggle's icons are Phosphor's, like every other icon in Snug.

docs/design.md section 3: one icon set, vendored from phosphor-icons/core v2.0.8
under its MIT licence. The toggle once drew its own sun and moon, copied from
Feather and Lucide, which would each need their own credit.
"""

import re
from pathlib import Path

import pytest
from django.test import Client

ICONS = Path(__file__).parent.parent / "snug" / "static" / "snug" / "icons"
TOGGLE = {"sun": "sun", "moon": "moon", "auto": "circle-half"}  # class: Phosphor icon


@pytest.mark.django_db  # the sign-in page reads the company's name
def test_the_toggle_uses_the_vendored_phosphor_icons() -> None:
    page = Client().get("/admin/login", secure=True).content.decode()
    toggle = re.search(r'<button class="theme-toggle".*?</button>', page, re.S)
    assert toggle, "no theme toggle on the page"
    svgs = re.findall(r"<svg[^>]*>.*?</svg>", toggle.group(0), re.S)
    assert len(svgs) == 3
    for svg, (cls, name) in zip(svgs, TOGGLE.items(), strict=True):
        assert f'class="icon {cls}"' in svg
        path = re.search(r"<path .*?/>", (ICONS / f"{name}.svg").read_text()).group(0)  # type: ignore[union-attr]
        assert path in svg, name


def test_the_licences_credit_nothing_else_for_it() -> None:
    licences = (Path(__file__).parent.parent / "THIRD_PARTY_LICENCES.md").read_text()
    assert "Feather" not in licences
    assert "theme toggle" not in licences
