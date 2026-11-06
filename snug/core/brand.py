"""The founder's accent colour, made readable wherever the viewer uses it.

The viewer uses three shades: the fill of a button (--brand), text and links
(--brand-text), and the label on a button (--on-brand). Each must reach 4.5:1
(WCAG AA) against what it sits on, in light and in dark mode. A colour that
would not is nudged towards black or white, in small steps, until it does.
"""

import hashlib
import re
from dataclasses import dataclass

MINIMUM = 4.5
# The viewer's backgrounds and surfaces (DESIGN.md section 3.4).
LIGHT_SURFACES = ("#fafaf9", "#ffffff")
DARK_SURFACES = ("#1c1917", "#262220")
# Labels on a button: white, or the viewer's darkest text colour.
WHITE = "#ffffff"
INK = "#1c1917"

_HEX = re.compile(r"^#?([0-9a-f]{3}|[0-9a-f]{6})$")


@dataclass(frozen=True)
class Shades:
    fill_light: str
    fill_dark: str
    text_light: str
    text_dark: str
    on_light: str
    on_dark: str
    adjusted: bool  # any shade differs from the colour chosen


def parse(raw: str) -> str | None:
    """A colour as `#rrggbb` in lower case, from `#RGB`, `RRGGBB` and the like, or None."""
    found = _HEX.match(raw.strip().lower())
    if not found:
        return None
    digits = found.group(1)
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    return f"#{digits}"


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _luminance(colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """The WCAG contrast ratio between two colours, from 1 to 21."""
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _mix(colour: str, towards: str, amount: float) -> str:
    mixed = (round(c + (t - c) * amount) for c, t in zip(_rgb(colour), _rgb(towards), strict=True))
    return "#" + "".join(f"{v:02x}" for v in mixed)


def _until_readable(colour: str, towards: str, against: tuple[str, ...]) -> str:
    """The least nudge of `colour` towards `towards` that reads on every surface."""
    for step in range(51):
        candidate = _mix(colour, towards, step / 50)
        if all(contrast(candidate, s) >= MINIMUM for s in against):
            return candidate
    return towards


def _button(colour: str) -> tuple[str, str]:
    """A button fill and its label: the label that reads better, the fill nudged if needed."""
    label = WHITE if contrast(colour, WHITE) >= contrast(colour, INK) else INK
    # A white label needs a darker fill; a dark label, a lighter one.
    fill = _until_readable(colour, "#000000" if label == WHITE else WHITE, (label,))
    return fill, label


def palette(accent: str) -> Shades:
    fill_light, on_light = _button(accent)
    fill_dark, on_dark = _button(accent)
    text_light = _until_readable(accent, "#000000", LIGHT_SURFACES)
    text_dark = _until_readable(accent, WHITE, DARK_SURFACES)
    shades = (fill_light, fill_dark, text_light, text_dark)
    return Shades(
        fill_light=fill_light,
        fill_dark=fill_dark,
        text_light=text_light,
        text_dark=text_dark,
        on_light=on_light,
        on_dark=on_dark,
        adjusted=any(shade != accent for shade in shades),
    )


def version(accent: str) -> str:
    """A short fingerprint for the stylesheet's address, so a change is fetched at once."""
    return hashlib.sha256(accent.encode("ascii")).hexdigest()[:10]


def stylesheet(accent: str) -> str:
    shades = palette(accent)
    return (
        "/* The founder's accent colour, made readable by Snug. */\n"
        ".v {\n"
        f"    --brand: light-dark({shades.fill_light}, {shades.fill_dark});\n"
        f"    --brand-text: light-dark({shades.text_light}, {shades.text_dark});\n"
        f"    --on-brand: light-dark({shades.on_light}, {shades.on_dark});\n"
        "}\n"
    )
