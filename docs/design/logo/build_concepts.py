"""Build docs/design/logo/concepts.html: Snug icon concepts on one sheet.

Run: python3 docs/design/logo/build_concepts.py

Glyphs are drawn on a 200 x 200 grid with round-ended 13-unit strokes, in
the manner of the Negroni glyph. `line` is a stroke in the glyph colour,
`fill` a solid shape in the glyph colour, and `knock` draws in the tile
colour, to cut a shape out of a solid one.
"""

import pathlib

HERE = pathlib.Path(__file__).resolve().parent

SPARKLE = '<path class="fill" d="M40 26 Q42.5 43.5 60 46 Q42.5 48.5 40 66 Q37.5 48.5 20 46 Q37.5 43.5 40 26 Z"/>'
DOOR = '<path class="line" d="M64 162 V44 H136 V162"/><path class="line" d="M46 162 H154"/>'
PAGE_PANE = (
    '<path class="fill" d="M80 58 H109 L122 71 V106 Q122 112 116 112 H86 Q80 112 80 106 V64 Q80 58 86 58 Z"/>'
    '<path class="knock thin" d="M109 58 V71 H122"/>'
)
GLASS_PANE = '<rect class="fill" x="78" y="58" width="44" height="54" rx="6"/>'
S_SMALL = (
    '<path class="knock mid" d="M112 73 C109.5 68.5 105 66.5 100 66.5 C93.5 66.5 89 69.5 89 74.5 '
    'C89 85.5 112 81 112 93 C112 98.5 106.5 102.5 100 102.5 C94.5 102.5 90 100.5 87.5 96"/>'
)
KEYHOLE = (
    '<circle class="fill" cx="100" cy="130" r="8.5"/>'
    '<path class="fill" d="M96 135 L93 149 Q92.6 151 94.6 151 H105.4 Q107.4 151 107 149 L104 135 Z"/>'
)
KNOB = '<circle class="fill" cx="119" cy="134" r="6.5"/>'

CONCEPTS = [
    # Round 2: doors
    (
        "door-s-knob",
        "5. The snug door, half-glazed",
        "A square-topped door on its threshold, with a glass pane carrying the s, as pub snug doors carried "
        "their name etched in the glass. A knob makes it a door at any size.",
        SPARKLE + DOOR + GLASS_PANE + S_SMALL + KNOB,
        True,
    ),
    (
        "door-s-key",
        "6. The snug door, locked",
        "The same door with the knob replaced by a keyhole: a room of your own, and it locks.",
        SPARKLE + DOOR + GLASS_PANE + S_SMALL + KEYHOLE,
        True,
    ),
    (
        "door-page-key",
        "7. The page in the door",
        "The pane becomes a dog-eared page and the lock a keyhole, so the door says documents and privacy "
        "without a letter.",
        SPARKLE + DOOR + PAGE_PANE + KEYHOLE,
        True,
    ),
    (
        "door-is-page",
        "8. The door that is a page",
        "The door itself has a folded top corner, so it reads as both a door and a document, with a keyhole "
        "where the handle would be.",
        SPARKLE
        + '<path class="line" d="M64 162 V44 H114 L136 66 V162"/><path class="line" d="M114 46 V66 H134"/>'
        '<path class="line" d="M46 162 H154"/>'
        '<circle class="fill" cx="100" cy="110" r="11"/>'
        '<path class="fill" d="M94.5 117 L90.5 136 Q90 139 93 139 H107 Q110 139 109.5 136 L105.5 117 Z"/>',
        True,
    ),
    # Round 1, kept for comparison
    (
        "door-s",
        "1. The snug door (round 1)",
        "The arched doorway with an s. Retired: the arch on a base line reads as a gravestone.",
        SPARKLE
        + '<path class="line" d="M60 158 V96 A40 40 0 0 1 140 96 V158"/><path class="line" d="M44 160 H156"/>'
        '<path class="line" d="M118 106 C114 99 107 96 100 96 C90 96 83 101 83 109 C83 126 118 119 118 137 '
        'C118 145 110 150 100 150 C92 150 85 147 81 140"/>',
        False,
    ),
    (
        "page-key",
        "3. The private page (round 1)",
        "A folded page with a keyhole. Clear, but generic.",
        SPARKLE
        + '<path class="line" d="M70 46 H118 L146 74 V158 H70 Z"/><path class="line" d="M118 48 V74 H144"/>'
        '<circle class="fill" cx="108" cy="106" r="13"/>'
        '<path class="fill" d="M102 114 L97 138 Q96.5 141 99.5 141 H116.5 Q119.5 141 119 138 L114 114 Z"/>',
        False,
    ),
    (
        "page-s",
        "4. The page monogram (round 1)",
        "A folded page holding an s. Clear at small sizes, but it can read as a file-type icon.",
        SPARKLE
        + '<path class="line" d="M68 40 H120 L148 68 V162 H68 Z"/><path class="line" d="M120 42 V68 H146"/>'
        '<path class="line" d="M127 100 C123 93 116 90 108 90 C98 90 91 95 91 103 C91 120 127 113 127 131 '
        'C127 139 119 145 108 145 C100 145 93 142 89 135"/>',
        False,
    ),
]


def tile(sym: str, cls: str, label: str | None = None) -> str:
    a11y = f'role="img" aria-label="{label}"' if label else 'aria-hidden="true"'
    return (
        f'<svg class="{cls}" viewBox="0 0 200 200" {a11y}>'
        f'<rect class="tile" width="200" height="200"/><use href="#g-{sym}"/></svg>'
    )


symbols = "\n".join(
    f'    <symbol id="g-{key}" viewBox="0 0 200 200">{body}</symbol>'
    for key, _, _, body, _ in CONCEPTS
)

sections = []
current_round = None
for key, title, blurb, _, round2 in CONCEPTS:
    rnd = "Round 2: doors" if round2 else "Round 1, for comparison"
    if rnd != current_round:
        sections.append(f'  <h2 class="round">{rnd}</h2>')
        current_round = rnd
    sections.append(f"""  <section class="concept">
    <h3>{title}</h3>
    <p>{blurb}</p>
    <div class="ways">
      {tile(key, "tile-lg cw-campari", title + ", Campari")}
      {tile(key, "tile-lg cw-vermouth", title + ", Vermouth")}
      {tile(key, "tile-lg cw-citrus", title + ", Citrus")}
      <div class="sizes">{tile(key, "s64 cw-campari")}{tile(key, "s32 cw-campari")}{tile(key, "s16 cw-campari")}</div>
    </div>
    <div class="lockup">{tile(key, "cw-campari")}<span class="word">Snug</span><span class="co">Fernlea Robotics</span></div>
  </section>""")

page = f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Snug: icon concepts</title>
<link rel="stylesheet" href="../snug.css">
<link rel="stylesheet" href="concepts.css">
</head>
<body>
<!-- Generated by build_concepts.py. Edit that, not this. -->
<svg class="defs" aria-hidden="true"><defs>
{symbols}
</defs></svg>
<main class="sheet">
  <h1>Snug icon concepts</h1>
  <p class="intro">A snug is the small private room in a British pub. Each concept follows the Negroni glyph: chunky round-ended strokes, a container with something inside it, and the four-pointed sparkle.</p>
{chr(10).join(sections)}
</main>
</body>
</html>
"""
(HERE / "concepts.html").write_text(page, encoding="utf-8")
print("written", len(page), "bytes,", len(CONCEPTS), "concepts")
