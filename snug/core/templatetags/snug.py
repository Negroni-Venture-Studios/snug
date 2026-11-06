"""Template tags for Snug's inline SVG: Phosphor icons and the logo tile."""

import re
from functools import cache

from django import template
from django.conf import settings
from django.utils.safestring import SafeString, mark_safe

register = template.Library()

_STATIC = settings.BASE_DIR / "static" / "snug"
_NAME = re.compile(r"^[a-z0-9-]+$")
_SUFFIX = re.compile(r"^[A-Za-z0-9]+$")


@cache
def _read(relative: str) -> str:
    return (_STATIC / relative).read_text(encoding="utf-8").strip()


@register.simple_tag
def icon(name: str, extra_class: str = "") -> SafeString:
    """A Phosphor icon (or the GitHub mark), inline, coloured by the surrounding text."""
    if not _NAME.match(name) or (extra_class and not _NAME.match(extra_class)):
        raise template.TemplateSyntaxError(f"Bad icon name or class: {name!r} {extra_class!r}")
    classes = f"icon {extra_class}".strip()
    svg = _read(f"icons/{name}.svg").replace(
        "<svg ", f'<svg class="{classes}" aria-hidden="true" ', 1
    )
    return mark_safe(svg)  # noqa: S308 - our own vendored files, never user input


@register.simple_tag
def logo_tile(suffix: str = "a") -> SafeString:
    """The Snug tile, inline. Each copy on a page needs its own mask id."""
    if not _SUFFIX.match(suffix):
        raise template.TemplateSyntaxError(f"Bad logo suffix: {suffix!r}")
    glyph = _read("img/snug-glyph.svg")
    inner = glyph.split(">", 1)[1].rsplit("</svg>", 1)[0]
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S).replace("snug-pane", f"snug-pane-{suffix}")
    return mark_safe(  # noqa: S308 - our own vendored file, never user input
        '<svg class="brand-tile" viewBox="0 0 200 200" aria-hidden="true">'
        '<rect class="tile" width="200" height="200"/>' + inner.strip() + "</svg>"
    )
