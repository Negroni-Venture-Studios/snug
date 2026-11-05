"""Every template obeys the CSP and the interface copy rules."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from django import template
from django.template.loader import render_to_string

TEMPLATES = sorted((Path(__file__).resolve().parent.parent / "snug" / "templates").rglob("*.html"))


class _CSPChecker(HTMLParser):
    """Finds everything a strict CSP forbids, the way a browser parses it.

    A real HTML parser, not a regular expression: it treats "</script >",
    "</SCRIPT>" and unclosed tags as browsers do, so nothing slips past.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {name.lower(): (value or "") for name, value in attrs}
        if tag == "script":
            self._in_script = True
            if "src" not in names:
                self.problems.append("script without src")
        if tag == "style":
            self.problems.append("<style> element")
        if "style" in names:
            self.problems.append(f"style attribute on <{tag}>")
        for name, value in names.items():
            if name.startswith("on"):
                self.problems.append(f"{name} handler on <{tag}>")
            if value.strip().lower().startswith("javascript:"):
                self.problems.append(f"javascript: URL in {name}")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.problems.append("inline script content")


def csp_problems(html: str) -> list[str]:
    checker = _CSPChecker()
    checker.feed(html)
    checker.close()
    return checker.problems


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        "<script>alert(1)</script >",
        "<SCRIPT>alert(1)</SCRIPT>",
        '<script src="/a.js">alert(1)</script>',
        "<script>alert(1)",
        "<style>body{}</style>",
        '<p style="color:red">x</p>',
        '<a href="#" onclick="go()">x</a>',
        '<a href=" JavaScript:go()">x</a>',
        '<img src=x ONERROR="go()">',
    ],
)
def test_the_checker_catches_what_the_csp_forbids(html: str) -> None:
    assert csp_problems(html)


def test_the_checker_allows_loading_a_script_file() -> None:
    assert csp_problems('<script src="/static/a.js" defer></script >') == []


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_inline_script_style_or_handlers(path: Path) -> None:
    assert csp_problems(path.read_text(encoding="utf-8")) == [], path.name


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_copy_has_no_dashes_or_middle_dots(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for character, name in (("\u2014", "em dash"), ("\u2013", "en dash"), ("\u00b7", "middle dot")):
        assert character not in text, f"{name} in {path.name}"


class _FormChecker(HTMLParser):
    """Lists POST forms that do not contain {% csrf_token %}."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[bool] = []  # one entry per POST form: has a token
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form" and (dict(attrs).get("method") or "").lower() == "post":
            self.forms.append(False)
            self._depth = 1
        elif tag == "form" and self._depth:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth and "{% csrf_token %}" in data:
            self.forms[-1] = True


def posting_forms_without_token(html: str) -> int:
    checker = _FormChecker()
    checker.feed(html)
    checker.close()
    return checker.forms.count(False)


def test_the_form_checker_catches_a_missing_token() -> None:
    assert posting_forms_without_token('<form method="post"><button></button></form >') == 1
    assert posting_forms_without_token('<FORM METHOD="POST">x</FORM>') == 1
    assert posting_forms_without_token('<form method="post">{% csrf_token %}</form>') == 0
    assert posting_forms_without_token('<form method="get">x</form>') == 0


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_posting_forms_carry_a_csrf_token(path: Path) -> None:
    assert posting_forms_without_token(path.read_text(encoding="utf-8")) == 0, path.name


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
