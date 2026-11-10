"""Every text colour in the stylesheet reads at 4.5:1 or better on what it sits on.

AGENTS.md asks for WCAG AA on every text pairing, in both themes. This reads
the tokens from snug.css, takes each side of every light-dark() pair, blends
the translucent tints over the surfaces beneath them, and measures each text
colour against everything it is used on. It also checks that the "Lowest
contrast" figures in DESIGN.md section 3.4 are the ones measured here.

The founder's accent shades come from /brand/brand.css, chosen by the server
for any colour; tests/test_brand_colour.py covers those.
"""

import re
from pathlib import Path

import pytest

from snug.core.brand import contrast

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "snug" / "static" / "snug" / "css" / "snug.css").read_text()
DESIGN = (ROOT / "DESIGN.md").read_text()

AA = 4.5
THEMES = ("light", "dark")
SURFACES = ("--bg", "--surface", "--tint")
# Text colours, each read on every surface, and the admin's status colours
# also on their own tints, as status labels and notices show them.
TEXT = {
    "admin": ("--text", "--text-2", "--text-3", "--link", "--ok", "--warn", "--danger"),
    "viewer": ("--text", "--text-2", "--text-3", "--danger"),
}
ON_TINT = {"--ok": "--ok-tint", "--warn": "--warn-tint", "--danger": "--danger-tint"}

Tokens = dict[str, tuple[str, str]]


def _split(value: str) -> tuple[str, str]:
    """light-dark(a, b) as (a, b); any other value is the same in both themes."""
    if not value.startswith("light-dark("):
        return value, value
    inner = value[len("light-dark(") : -1]
    depth = 0
    for index, char in enumerate(inner):
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            return inner[:index].strip(), inner[index + 1 :].strip()
    raise ValueError(value)


def tokens(selector: str) -> Tokens:
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", CSS, re.S)
    assert block, selector
    found = re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", block.group(1))
    return {name: _split(value.strip()) for name, value in found}


PALETTES = {"admin": tokens(":root"), "viewer": {**tokens(":root"), **tokens(".v")}}


def colour(palette: Tokens, name: str, theme: str) -> str:
    value = palette[name][THEMES.index(theme)]
    reference = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    return colour(palette, reference.group(1), theme) if reference else value


def _rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def over(tint: str, under: str) -> str:
    """A translucent rgba() colour as it shows over a solid one."""
    red, green, blue, alpha = (float(n) for n in re.findall(r"[\d.]+", tint))
    mixed = (
        round(top * alpha + bottom * (1 - alpha))
        for top, bottom in zip((red, green, blue), _rgb(under), strict=True)
    )
    return "#" + "".join(f"{value:02x}" for value in mixed)


def backgrounds(palette: Tokens, text: str, theme: str) -> dict[str, str]:
    """Everything this text colour is shown on, by name."""
    found = {name: colour(palette, name, theme) for name in SURFACES}
    if text in ON_TINT:
        tint = colour(palette, ON_TINT[text], theme)
        for name in ("--bg", "--surface"):
            found[f"{ON_TINT[text]} over {name}"] = over(tint, found[name])
    return found


def lowest(palette: Tokens, text: str, theme: str) -> float:
    ink = colour(palette, text, theme)
    return min(contrast(ink, under) for under in backgrounds(palette, text, theme).values())


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("name", ["admin", "viewer"])
def test_every_text_colour_reads_on_everything_beneath_it(name: str, theme: str) -> None:
    palette = PALETTES[name]
    failing = []
    for text in TEXT[name]:
        ink = colour(palette, text, theme)
        for under_name, under in backgrounds(palette, text, theme).items():
            ratio = contrast(ink, under)
            if ratio < AA:
                failing.append(f"{text} {ink} on {under_name} {under}: {ratio:.2f}")
    assert not failing


@pytest.mark.parametrize("theme", THEMES)
def test_the_primary_button_text_reads_on_the_action_colour(theme: str) -> None:
    palette = PALETTES["admin"]
    action = colour(palette, "--action", theme)
    assert contrast(colour(palette, "--on-action", theme), action) >= AA


def table_rows() -> dict[str, str]:
    """The admin palette's "Lowest contrast" column in DESIGN.md, by token."""
    section = DESIGN[DESIGN.index("**Admin palette**") : DESIGN.index("**Viewer palette**")]
    rows = re.findall(r"^\| `(--[a-z0-9-]+)`[^\n]*\| ([0-9.]+(?: / [0-9.]+)?) \|$", section, re.M)
    return dict(rows)


def test_the_design_figures_are_the_measured_ones() -> None:
    palette = PALETTES["admin"]
    rows = table_rows()
    assert set(rows) == {*TEXT["admin"], "--action"}
    for text in TEXT["admin"]:
        measured = " / ".join(f"{lowest(palette, text, theme):.1f}" for theme in THEMES)
        assert rows[text] == measured, text
    button = contrast(colour(palette, "--on-action", "light"), colour(palette, "--action", "light"))
    assert rows["--action"] == f"{button:.1f}"
