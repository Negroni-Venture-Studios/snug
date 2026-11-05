"""The PDF worker: reads and renders untrusted PDFs in a separate, limited process.

    python -I -m snug.rendering.worker inspect PDF
    python -I -m snug.rendering.worker render PDF PAGE WIDTH OUT

It imports no Django and no Snug settings, so even a PDF that took over the
renderer would find no secret key and no database. It limits its own memory,
CPU time, output size and open files before it opens the file. Results and
errors go to standard output as one line of JSON.
"""

import json
import sys
from pathlib import Path
from typing import Any

MEMORY_BYTES = 1024 * 1024 * 1024  # 1 GiB of address space
CPU_SECONDS = 60
OUTPUT_BYTES = 64 * 1024 * 1024
OPEN_FILES = 32
MAX_PAGES = 2000
# A rendered page never exceeds this many pixels, or this length of side,
# however odd its shape. (WebP cannot exceed 16,383 pixels a side.)
MAX_PIXELS = 1600 * 4000
MAX_SIDE = 8000


def apply_limits() -> None:
    import resource

    limits = [
        (resource.RLIMIT_CPU, CPU_SECONDS),
        (resource.RLIMIT_FSIZE, OUTPUT_BYTES),
        (resource.RLIMIT_NOFILE, OPEN_FILES),
        (resource.RLIMIT_CORE, 0),
        # macOS does not enforce an address-space limit; Linux, where Snug
        # runs, does. Setting it may fail on macOS, which is fine there.
        (resource.RLIMIT_AS, MEMORY_BYTES),
    ]
    for kind, value in limits:
        try:
            resource.setrlimit(kind, (value, value))
        except (ValueError, OSError):
            if kind != resource.RLIMIT_AS:
                raise


def _fail(code: str) -> int:
    print(json.dumps({"error": code}))
    return 3


def _open(path: str) -> Any:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as error:
        if error.err_code in (pdfium_c.FPDF_ERR_PASSWORD, pdfium_c.FPDF_ERR_SECURITY):
            raise _Refused("password") from error
        raise _Refused("unreadable") from error


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def inspect(path: str) -> dict[str, Any]:
    pdf = _open(path)
    count = len(pdf)
    if count == 0:
        raise _Refused("empty")
    if count > MAX_PAGES:
        raise _Refused("too_many_pages")
    sizes = []
    for index in range(count):
        width, height = pdf[index].get_size()
        if width <= 0 or height <= 0:
            raise _Refused("unreadable")
        sizes.append([round(width, 2), round(height, 2)])
    return {"pages": count, "sizes": sizes}


def render(path: str, page: int, width: int, out: str) -> dict[str, Any]:
    pdf = _open(path)
    if not 1 <= page <= len(pdf):
        raise _Refused("no_such_page")
    pdf_page = pdf[page - 1]
    page_width, page_height = pdf_page.get_size()
    scale = width / page_width
    if (page_width * scale) * (page_height * scale) > MAX_PIXELS:
        scale = (MAX_PIXELS / (page_width * page_height)) ** 0.5
    scale = min(scale, MAX_SIDE / max(page_width, page_height))
    image = pdf_page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
    target = Path(out)
    partial = target.with_name(f".{target.name}.part")
    image.save(partial, "WEBP", quality=85, method=4)
    partial.replace(target)
    return {"width": image.width, "height": image.height}


def main(argv: list[str]) -> int:
    apply_limits()
    try:
        if len(argv) == 2 and argv[0] == "inspect":
            result = inspect(argv[1])
        elif len(argv) == 5 and argv[0] == "render":
            result = render(argv[1], int(argv[2]), int(argv[3]), argv[4])
        else:
            return _fail("usage")
    except _Refused as refused:
        return _fail(refused.code)
    except MemoryError:
        return _fail("memory")
    except Exception:  # noqa: BLE001 - any failure on hostile input is "unreadable"
        return _fail("unreadable")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
