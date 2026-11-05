"""Small real PDFs for tests, made with Pillow. Sizes are in PDF points."""

import io

from PIL import Image


def make_pdf(*sizes: tuple[int, int]) -> bytes:
    pages = [
        Image.new("RGB", size, colour) for size, colour in zip(sizes, _colours(), strict=False)
    ]
    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=72)
    return out.getvalue()


def _colours() -> list[tuple[int, int, int]]:
    return [(255, 98, 0), (255, 171, 26), (22, 156, 89), (26, 11, 0)] * 50


def chunks(data: bytes, size: int = 64 * 1024) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)] or [b""]
