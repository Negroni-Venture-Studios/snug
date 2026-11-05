"""Every template obeys the CSP and the interface copy rules."""

import re
from pathlib import Path

import pytest
from django import template
from django.template.loader import render_to_string

TEMPLATES = sorted((Path(__file__).resolve().parent.parent / "snug" / "templates").rglob("*.html"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_inline_script_style_or_handlers(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    # A script element may only load a file: nothing between its tags.
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script>", text, flags=re.S | re.I):
        assert "src=" in match.group(1), f"inline script in {path.name}"
        assert match.group(2).strip() == "", f"inline script in {path.name}"
    assert not re.search(r"<style\b", text, flags=re.I), f"<style> in {path.name}"
    assert not re.search(r"\sstyle\s*=", text, flags=re.I), f"style attribute in {path.name}"
    assert not re.search(r"\son[a-z]+\s*=", text, flags=re.I), f"inline handler in {path.name}"
    assert "javascript:" not in text.lower()


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_copy_has_no_dashes_or_middle_dots(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for character, name in (("\u2014", "em dash"), ("\u2013", "en dash"), ("\u00b7", "middle dot")):
        assert character not in text, f"{name} in {path.name}"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_posting_forms_carry_a_csrf_token(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for form in re.findall(r"<form\b[^>]*method=\"post\"[^>]*>.*?</form>", text, flags=re.S | re.I):
        assert "{% csrf_token %}" in form, f"POST form without a CSRF token in {path.name}"


def test_icon_tag_refuses_odd_names() -> None:
    with pytest.raises(template.TemplateSyntaxError):
        template.Template('{% load snug %}{% icon "../../settings" %}').render(template.Context())


def test_logo_tag_refuses_odd_suffixes() -> None:
    with pytest.raises(template.TemplateSyntaxError):
        template.Template('{% load snug %}{% logo_tile "a b" %}').render(template.Context())


def test_each_logo_has_its_own_mask() -> None:
    html = template.Template('{% load snug %}{% logo_tile "x" %}{% logo_tile "y" %}').render(
        template.Context()
    )
    assert 'id="snug-pane-x"' in html and 'id="snug-pane-y"' in html
    assert "url(#snug-pane-x)" in html and "url(#snug-pane-y)" in html


def test_csrf_failure_page_renders() -> None:
    assert "This form has expired" in render_to_string("core/csrf_failure.html")
