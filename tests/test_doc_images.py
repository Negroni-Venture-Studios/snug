"""The documentation's screenshots: each is used, and every picture a document shows exists.

tests/e2e/test_screenshots.py writes docs/images/screenshots/; a shot no
document shows is weight in the repository for nothing, and a path that
points nowhere is a broken picture on GitHub.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "images" / "screenshots"
# Screenshots of other services, taken by hand during a real deployment.
CAPTURED = [ROOT / "docs" / "images" / "digitalocean", ROOT / "docs" / "images" / "google"]
DOCUMENTS = [
    p for p in ROOT.glob("**/*.md") if ".venv" not in p.parts and "node_modules" not in p.parts
]
PICTURE = re.compile(r'(?:src|srcset)="([^"]+\.(?:png|svg))"')


def test_every_screenshot_is_shown_somewhere() -> None:
    text = "".join(p.read_text() for p in DOCUMENTS)
    unused = [
        p.name for p in sorted(SHOTS.glob("*.png")) if f"images/screenshots/{p.name}" not in text
    ]
    assert not unused


def test_every_picture_in_a_document_exists() -> None:
    missing = []
    for document in DOCUMENTS:
        for path in PICTURE.findall(document.read_text()):
            if not path.startswith("http") and not (document.parent / path).exists():
                missing.append(f"{document.relative_to(ROOT)}: {path}")
    assert not missing


def test_every_screenshot_has_a_light_and_a_dark_copy() -> None:
    names = {p.name for p in SHOTS.glob("*.png")}
    for name in names:
        other = (
            name.replace("-light.", "-dark.")
            if "-light." in name
            else name.replace("-dark.", "-light.")
        )
        assert other in names, name


def test_every_captured_screenshot_is_shown_somewhere() -> None:
    text = "".join(p.read_text() for p in DOCUMENTS)
    unused = [
        f"{folder.name}/{p.name}"
        for folder in CAPTURED
        for p in sorted(folder.glob("*.png"))
        if f"images/{folder.name}/{p.name}" not in text
    ]
    assert not unused
