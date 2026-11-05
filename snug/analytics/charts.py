"""Server-rendered SVG for the reading grid, as in the design mockups.

Every chart shares one coordinate system: each page gets a SLOT-unit column,
so the bar chart, the page axis and every viewer's strip line up at any
width. Geometry is set by attributes, which the CSP allows; colour comes from
the h0 to h4 classes in the stylesheet.
"""

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

SLOT = 20
MAX_LABELS = 12


def duration(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    if minutes == 0:
        return f"{rest} s"
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def heat(seconds: float, *, seen: bool) -> int:
    """0 for never seen; otherwise 1 to 4 by time. Seen briefly is never 0."""
    if not seen and seconds <= 0:
        return 0
    if seconds <= 20:
        return 1
    return 2 if seconds <= 45 else 3 if seconds <= 90 else 4


def strip(seconds: list[float], seen: list[bool], email: str) -> SafeString:
    width = SLOT * len(seconds)
    count = sum(seen)
    parts = [
        f'<svg class="strip" viewBox="0 0 {width} 16" preserveAspectRatio="none" role="img" '
        f'aria-label="{escape(email)}: saw {count} of {len(seconds)} pages">'
    ]
    for i, (secs, was_seen) in enumerate(zip(seconds, seen, strict=True)):
        tip = f"Page {i + 1}: {duration(secs)}" if was_seen else f"Page {i + 1}: not seen"
        parts.append(
            f'<rect class="h{heat(secs, seen=was_seen)}" x="{i * SLOT + 1}" y="0" '
            f'width="{SLOT - 2}" height="16" rx="2"><title>{tip}</title></rect>'
        )
    parts.append("</svg>")
    return mark_safe("".join(parts))  # noqa: S308 - built from numbers and escaped text


def bars(averages: list[float]) -> SafeString:
    width = SLOT * len(averages)
    top = max(averages) or 1
    parts = [
        f'<svg class="bars" viewBox="0 0 {width} 64" preserveAspectRatio="none" role="img" '
        'aria-label="Average time on each page, among viewers who reached it">'
    ]
    for i, average in enumerate(averages):
        height = max(2, round(62 * average / top, 1)) if average else 0
        parts.append(
            f'<rect class="h{heat(average, seen=average > 0)}" x="{i * SLOT + 3}" '
            f'y="{64 - height}" width="{SLOT - 6}" height="{height}" rx="1.5">'
            f"<title>Page {i + 1}: {duration(average)} on average</title></rect>"
        )
    parts.append("</svg>")
    return mark_safe("".join(parts))  # noqa: S308 - built from numbers only


def axis(pages: int) -> SafeString:
    """Page numbers above the columns: every page, or every few on a long document.

    HTML, not SVG: the charts stretch to fit, and stretched SVG text would be
    distorted. One equal flex item per page keeps the labels over their columns.
    """
    step = max(1, -(-pages // MAX_LABELS))
    shown = {*range(1, pages + 1, step), pages}
    if len(shown) > MAX_LABELS:
        shown.discard(max(shown - {pages}))
    labels = "".join(f"<span>{p if p in shown else ''}</span>" for p in range(1, pages + 1))
    return mark_safe(f'<span class="axis" aria-hidden="true">{labels}</span>')  # noqa: S308 - numbers only


def legend() -> SafeString:
    cells = "".join(
        f'<rect class="h{i}" x="{i * 18}" y="0" width="16" height="10" rx="2"/>' for i in range(5)
    )
    svg = f'<svg viewBox="0 0 90 10" aria-hidden="true">{cells}</svg>'
    return mark_safe(f'<span class="legend">Less{svg}More</span>')  # noqa: S308 - fixed markup
