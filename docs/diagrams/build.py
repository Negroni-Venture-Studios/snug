"""Build the documentation's diagrams in Snug's light and dark colours.

Run: python3 docs/diagrams/build.py

Each diagram is drawn by hand in docs/diagrams/<name>.svg, with classes in
place of colours (see PALETTE). This writes docs/images/diagrams/<name>-light.svg
and <name>-dark.svg, each with the palette and the Geist fonts inside it, since
GitHub shows an SVG as an image, which cannot load anything from outside. The
Markdown shows the one that matches the reader's theme:

    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/x-dark.svg">
      <img alt="..." src="images/diagrams/x-light.svg">
    </picture>

tests/test_diagrams.py fails if a built file is out of date.
"""

import base64
import pathlib
import re
import sys

DIAGRAMS = pathlib.Path(__file__).resolve().parent
ROOT = DIAGRAMS.parent.parent
OUT = ROOT / "docs" / "images" / "diagrams"
FONTS = ROOT / "snug" / "static" / "snug" / "fonts"

# The Negroni tokens from docs/design/snug.css: (light, dark).
TOKENS = {
    "bg": ("#fdfbf9", "#1A0B00"),
    "surface": ("#ffffff", "#241307"),
    "tint": ("#f7efe8", "#2e1b0d"),
    "border": ("#ece3db", "#3a2516"),
    "border-strong": ("#d8cabe", "#553a26"),
    "text": ("#1A0B00", "#f6e8dc"),
    "text-2": ("#6a5141", "#d2b9a5"),
    "text-3": ("#7a6253", "#ad927e"),
    "link": ("#B24400", "#FF8133"),
    "action": ("#FF6200", "#FF6200"),
    "on-action": ("#1A0B00", "#1A0B00"),
    "ok": ("#127D47", "#1ABC6B"),
    "warn": ("#8a5a00", "#FFAB1A"),
    "danger": ("#a8322b", "#f08a7e"),
    "heat-1": ("#F9E1B8", "#4d2c0e"),
    "heat-2": ("#FFAB1A", "#8f4a08"),
    "heat-3": ("#FF6200", "#d65a00"),
    "heat-4": ("#B24400", "#FF8133"),
}

# The classes a diagram may use. Fills and strokes only; text sizes are set here
# too, so every diagram reads at the same scale.
PALETTE = """
svg { background: var(--bg); }
text { font-family: Geist, sans-serif; font-size: 14px; fill: var(--text); }
.bg { fill: var(--bg); }
.box { fill: var(--surface); stroke: var(--border-strong); stroke-width: 1.25; }
.zone { fill: var(--tint); stroke: var(--border); stroke-width: 1; }
.zone-dashed {
  fill: none; stroke: var(--border-strong); stroke-width: 1.25; stroke-dasharray: 6 5;
}
.key { fill: var(--surface); stroke: var(--link); stroke-width: 1.75; }
.solid-action { fill: var(--action); }
.solid-ok { fill: var(--ok); }
.solid-warn { fill: var(--warn); }
.solid-danger { fill: var(--danger); }
.heat-1 { fill: var(--heat-1); } .heat-2 { fill: var(--heat-2); }
.heat-3 { fill: var(--heat-3); } .heat-4 { fill: var(--heat-4); }
.line { fill: none; stroke: var(--text-3); stroke-width: 1.5; }
.line-accent { fill: none; stroke: var(--link); stroke-width: 1.75; }
.line-ok { fill: none; stroke: var(--ok); stroke-width: 1.5; }
.line-danger { fill: none; stroke: var(--danger); stroke-width: 1.5; }
.line-dashed { fill: none; stroke: var(--text-3); stroke-width: 1.25; stroke-dasharray: 4 4; }
.arrow { fill: var(--text-3); }
.arrow-accent { fill: var(--link); }
.arrow-ok { fill: var(--ok); }
.arrow-danger { fill: var(--danger); }
.title { font-size: 15px; font-weight: 600; }
.label { font-size: 13px; fill: var(--text-2); }
.small { font-size: 12px; fill: var(--text-3); }
.heading { font-size: 12px; font-weight: 600; letter-spacing: 0.06em; fill: var(--text-3); }
.mono {
  font-family: "Geist Mono", monospace; font-size: 12.5px; fill: var(--text-2);
  font-variant-ligatures: none;
}
.accent { fill: var(--link); }
.ok { fill: var(--ok); }
.warn { fill: var(--warn); }
.danger { fill: var(--danger); }
.on-action { fill: var(--on-action); font-weight: 600; }
"""


def _font(name: str, family: str) -> str:
    data = base64.b64encode((FONTS / name).read_bytes()).decode()
    return (
        f'@font-face {{ font-family: "{family}"; font-weight: 100 900; '
        f'src: url(data:font/woff2;base64,{data}) format("woff2"); }}'
    )


def build(source: str, theme: int) -> str:
    """The diagram with its palette for one theme (0 light, 1 dark) and its fonts."""
    variables = "".join(f"--{name}: {pair[theme]}; " for name, pair in TOKENS.items())
    fonts = [_font("Geist-Variable.woff2", "Geist")]
    if 'class="mono' in source or " mono" in source:
        fonts.append(_font("GeistMono-Variable.woff2", "Geist Mono"))
    style = f"<style>{''.join(fonts)}\nsvg {{ {variables}}}{PALETTE}</style>"
    opening = re.search(r"<svg\b[^>]*>", source)
    if opening is None:
        raise ValueError("no <svg> element")
    end = opening.end()
    return source[:end] + "\n" + style + source[end:]


def outputs() -> dict[pathlib.Path, str]:
    built = {}
    for path in sorted(DIAGRAMS.glob("*.svg")):
        source = path.read_text()
        for theme, suffix in ((0, "light"), (1, "dark")):
            built[OUT / f"{path.stem}-{suffix}.svg"] = build(source, theme)
    return built


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    built = outputs()
    for path, text in built.items():
        path.write_text(text)
    for stale in set(OUT.glob("*.svg")) - set(built):
        stale.unlink()
    print(f"Built {len(built)} files in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
