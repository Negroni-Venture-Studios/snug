"""Build docs/design/mockups.html from mockups.template.html.

Run: python3 docs/design/build_mockups.py

Sample data only. Every figure on the page is computed from the per-page
seconds below, so the stats, the matrix and the chart agree with each other.
"""

import html
import pathlib
import re
import statistics

DESIGN = pathlib.Path(__file__).resolve().parent
PAGES = 18

# Seconds each viewer spent on each page (0 = never shown). Sample data.
VIEWERS = [
    ("sam@kittiwake.example", "Kittiwake Partners", 1, "Today 11:05", True, [24, 16] + [0] * 16),
    (
        "priya@northfield.example",
        "Partner, Northfield Capital",
        4,
        "Today 09:42",
        False,
        [38, 71, 52, 31, 162, 96, 58, 49, 171, 84, 33, 27, 88, 63, 104, 52, 29, 214],
    ),
    (
        "tom@harbourcap.example",
        "Associate, Harbour Capital",
        3,
        "Yesterday 16:20",
        False,
        [41, 66, 47, 28, 118, 73, 44, 39, 136, 61, 22, 19, 57, 41, 80, 36, 18, 120],
    ),
    (
        "j.ortiz@example.com",
        "Adviser",
        3,
        "4 Nov 2026",
        False,
        [22, 47, 31, 19, 74, 52, 36, 30, 83, 47, 14, 12, 39, 26, 51, 21, 12, 54],
    ),
    (
        "a.mensah@quarry.example",
        "Quarry Angels",
        2,
        "3 Nov 2026",
        False,
        [30, 52, 44, 27, 96, 61, 40, 33, 112, 48, 21, 14] + [0] * 6,
    ),
    (
        "lena@foxglove.example",
        "Foxglove Ventures",
        1,
        "2 Nov 2026",
        False,
        [26, 31, 22, 13] + [0] * 14,
    ),
]


def dur(s: int) -> str:
    m, s = divmod(s, 60)
    if m == 0:
        return f"{s} s"
    return f"{m} min {s} s" if s else f"{m} min"


def heat(s: float) -> int:
    if s <= 0:
        return 0
    return 1 if s <= 20 else 2 if s <= 45 else 3 if s <= 90 else 4


SLOT = 20  # each page gets a 20-unit slot in a 360-unit-wide viewBox


def strip(secs: list[int], email: str) -> str:
    seen = sum(1 for s in secs if s)
    parts = [
        f'<svg class="strip" viewBox="0 0 360 16" preserveAspectRatio="none" role="img" '
        f'aria-label="{html.escape(email)}: saw {seen} of {PAGES} pages">'
    ]
    for i, s in enumerate(secs):
        tip = f"Page {i + 1}: {dur(s)}" if s else f"Page {i + 1}: not seen"
        parts.append(
            f'<rect class="h{heat(s)}" x="{i * SLOT + 1}" y="0" width="{SLOT - 2}" height="16" rx="2">'
            f"<title>{tip}</title></rect>"
        )
    parts.append("</svg>")
    return "".join(parts)


def bars(avgs: list[float]) -> str:
    top = max(avgs)
    parts = [
        '<svg class="bars" viewBox="0 0 360 64" preserveAspectRatio="none" role="img" '
        'aria-label="Average time on each page, among viewers who reached it">'
    ]
    for i, a in enumerate(avgs):
        h = max(2, round(62 * a / top, 1))
        parts.append(
            f'<rect class="h{heat(a)}" x="{i * SLOT + 3}" y="{64 - h}" width="{SLOT - 6}" height="{h}" rx="1.5">'
            f"<title>Page {i + 1}: {dur(round(a))} on average</title></rect>"
        )
    parts.append("</svg>")
    return "".join(parts)


def icon(name: str, extra_class: str = "") -> str:
    svg = (DESIGN / "icons" / f"{name}.svg").read_text(encoding="utf-8").strip()
    classes = f"icon {extra_class}".strip()
    return svg.replace("<svg ", f'<svg class="{classes}" aria-hidden="true" ', 1)


def logo(suffix: str) -> str:
    """The Snug tile, inline. Each copy needs its own mask id."""
    glyph = (DESIGN / "logo" / "snug-glyph.svg").read_text(encoding="utf-8")
    inner = glyph.split(">", 1)[1].rsplit("</svg>", 1)[0]
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S).replace("snug-pane", f"snug-pane-{suffix}")
    return (
        '<svg class="brand-tile" viewBox="0 0 200 200" aria-hidden="true">'
        '<rect class="tile" width="200" height="200"/>' + inner.strip() + "</svg>"
    )


TOGGLE = (
    '<button class="theme-toggle" type="button" aria-label="Theme: dark" title="Theme: dark">'
    + icon("sun", "sun")
    + icon("moon", "moon")
    + icon("circle-half", "auto")
    + "</button>"
)

totals = [sum(v[5]) for v in VIEWERS]
avgs = []
for p in range(PAGES):
    seen = [v[5][p] for v in VIEWERS if v[5][p]]
    avgs.append(sum(seen) / len(seen))
read_all = sum(1 for v in VIEWERS if all(v[5]))
visits = sum(v[2] for v in VIEWERS)
median = round(statistics.median(totals))
ranked = sorted(range(PAGES), key=lambda p: avgs[p], reverse=True)[:2]
longest = " and ".join(f"page {p + 1} ({dur(round(avgs[p]))})" for p in ranked)

rows = []
for (email, note, n, last, new, secs), total in zip(VIEWERS, totals, strict=True):
    seen = sum(1 for s in secs if s)
    tag = '<span class="new">New</span>' if new else ""
    rows.append(f"""
      <div class="row">
        <div class="cell who"><span class="email">{email}{tag}</span><span class="note">{note}</span></div>
        <div class="cell strip-cell">{strip(secs, email)}</div>
        <div class="cell r first">{n}<span class="visually-hidden"> {"visit" if n == 1 else "visits"}</span></div>
        <div class="cell r">{dur(total)}</div>
        <div class="cell r last">{last}</div>
      </div>""")

axis = "".join(f"<span>{p + 1}</span>" for p in range(PAGES))
legend = (
    '<span class="legend">Less<svg viewBox="0 0 90 10" aria-hidden="true">'
    + "".join(
        f'<rect class="h{i}" x="{i * 18}" y="0" width="16" height="10" rx="2"/>' for i in range(5)
    )
    + "</svg>More</span>"
)

page = (DESIGN / "mockups.template.html").read_text(encoding="utf-8")
subs = {
    "VIEWERS": str(len(VIEWERS)),
    "VISITS": str(visits),
    "READALL": f"{read_all}",
    "MEDIAN_MIN": str(median // 60),
    "MEDIAN_S": str(median % 60),
    "ROWS": "".join(rows),
    "toggle": TOGGLE,
    "BARS": bars(avgs),
    "AXIS": axis,
    "LEGEND": legend,
    "LONGEST": longest,
}
for k, val in subs.items():
    page = page.replace("{{" + k + "}}", val)
page = re.sub(r"\{\{icon:([a-z-]+)\}\}", lambda m: icon(m.group(1)), page)
page = re.sub(r"\{\{logo:([A-Za-z0-9]+)\}\}", lambda m: logo(m.group(1)), page)
assert "{{" not in page, re.findall(r"\{\{[^}]+\}\}", page)
# A placeholder that comes out empty leaves its indent behind.
page = "\n".join(line.rstrip() for line in page.split("\n"))
(DESIGN / "mockups.html").write_text(page, encoding="utf-8")
print("written", len(page), "bytes; median", dur(median), "; longest", longest)
