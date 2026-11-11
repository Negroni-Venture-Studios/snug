"""Watermarks: stamped on each request, inside the confined worker.

The cached page came out of the worker, which reads untrusted PDFs. Decoding
it in Snug's own process would hand a compromised worker a way to attack
Snug's image decoder, so the stamp is applied in a fresh worker too, and Snug
only checks the container format of what comes back.
"""

import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from snug.rendering import pages, sandbox, worker
from tests.pdfs import make_pdf

EMAIL = "priya@northfield.example"
DATE = "5 Nov 2026"
REF = "7Q2K"


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    deck = tmp_path / "deck.pdf"
    deck.write_bytes(make_pdf((960, 540)))
    path = tmp_path / "page.webp"
    path.write_bytes(sandbox.render(deck, 1, 800))
    return path


def image(webp: bytes) -> Image.Image:
    with Image.open(io.BytesIO(webp)) as opened:
        assert opened.format == "WEBP"
        return opened.convert("RGB")


def stamp(path: Path, email: str = EMAIL) -> bytes:
    return sandbox.stamp(path, pages.WATERMARK_FONT, email=email, date=DATE, ref=REF)


def test_the_stamp_adds_a_footer_and_marks_the_page(plain: Path) -> None:
    before = image(plain.read_bytes())
    after = image(stamp(plain))
    assert after.width == before.width
    footer = after.height - before.height
    assert 28 <= footer <= 60
    # The page itself carries the tiled mark: it is no longer the plain page.
    page_part = after.crop((0, 0, before.width, before.height))
    changed = (
        ImageChops.difference(page_part, before).convert("L").point(lambda v: 255 if v > 12 else 0)
    )
    # Thin diagonal text covers well under 1% of a page; none at all would be 0.
    assert changed.histogram()[255] > before.width * before.height * 0.003
    # The footer holds text: it is not a single flat colour.
    band = after.crop((0, before.height, after.width, after.height))
    assert len(band.getcolors(maxcolors=1 << 20) or []) > 2


def test_each_viewer_gets_a_different_stamp(plain: Path) -> None:
    assert stamp(plain) != stamp(plain, email="sam@eastgate.example")


@pytest.mark.parametrize(
    ("email", "ref"),
    [("x" * 300, REF), ("line\nbreak@example.com", REF), (EMAIL, "not a ref!"), (EMAIL, "")],
)
def test_odd_stamp_text_is_refused(plain: Path, email: str, ref: str) -> None:
    with pytest.raises(sandbox.WorkerError):
        sandbox.stamp(plain, pages.WATERMARK_FONT, email=email, date=DATE, ref=ref)


def test_only_webp_is_stamped(tmp_path: Path) -> None:
    png = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(png, "PNG")
    with pytest.raises(sandbox.WorkerError):
        stamp(png)


def test_oversized_images_are_refused(tmp_path: Path) -> None:
    big = tmp_path / "big.webp"
    Image.new("RGB", (worker.MAX_SIDE + 8, 64), "white").save(big, "WEBP")
    with pytest.raises(sandbox.WorkerError):
        stamp(big)


@pytest.mark.parametrize(
    ("size", "full"),
    [((8000, 800), False), ((800, 8000), False), ((1600, 2263), True), ((800, 450), True)],
)
def test_the_mark_square_is_bounded_whatever_the_page_shape(
    size: tuple[int, int], full: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Security audit follow-up, 11 November 2026: the mark is drawn on a square
    # as wide as the page's diagonal, so a long thin page within MAX_SIDE and
    # MAX_PIXELS asked for a square of 8,000 pixels a side (256 MB of RGBA).
    squares: list[int] = []
    new = Image.new

    def spy(mode: str, dimensions: tuple[int, int], *args: object) -> Image.Image:
        if mode == "RGBA" and dimensions[0] == dimensions[1]:
            side = dimensions[0]
            assert side * side <= 2 * worker.MAX_PIXELS + 4 * side, dimensions
            squares.append(side)
        return new(mode, dimensions, *args)  # type: ignore[arg-type]

    page = io.BytesIO()
    Image.new("RGB", size, "white").save(page, "WEBP")
    monkeypatch.setattr(Image, "new", spy)
    stamped = image(
        worker.stamp(page.getvalue(), pages.WATERMARK_FONT.read_bytes(), EMAIL, DATE, REF)
    )
    assert stamped.width == size[0] and stamped.height > size[1]
    width, height = size
    # An ordinary page keeps its mark exactly: drawn at full size, not enlarged.
    assert (squares == [int((width**2 + height**2) ** 0.5) + 1]) is full


# ---------------------------------------------------------------- in-process, for coverage


def _fd(path: Path) -> str:
    return str(os.open(path, os.O_RDONLY))


def test_main_stamps_in_process(plain: Path, capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    args = ["stamp", _fd(plain), _fd(pages.WATERMARK_FONT), EMAIL, DATE, REF]
    assert worker.main(args, confined=False) == 0
    assert image(capsysbinary.readouterr().out).width == 800


def test_main_refuses_bad_stamp_arguments(plain: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["stamp", _fd(plain), _fd(pages.WATERMARK_FONT), EMAIL, DATE]
    assert worker.main(args, confined=False) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "usage"}
