"""The PDF worker: reads and renders untrusted PDFs in a separate, confined process.

    python -I -m snug.rendering.worker inspect FD
    python -I -m snug.rendering.worker render FD PAGE WIDTH

Snug opens the PDF and passes the open file as descriptor FD; the worker is
never given a path. `inspect` prints one line of JSON. `render` writes the
WebP image to standard output. Errors print {"error": ...} and exit with 3.

Before it touches the PDF, the worker
  - imports everything it will need,
  - limits its own memory, CPU time, file size and open files, and
  - on Linux, confines itself with Landlock: from then on it cannot open any
    file except system fonts (read-only), or make network connections.
It imports no Django and no Snug settings, and its environment is empty of
secrets, so even a PDF that took over the renderer would find nothing to take.
"""

import io
import json
import os
import sys
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
FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "/etc/fonts")


def _is_linux() -> bool:
    # A function, so type checkers on macOS and Linux both see every branch.
    return sys.platform == "linux"


def apply_limits() -> None:
    import resource

    limits = [
        (resource.RLIMIT_CPU, CPU_SECONDS),
        (resource.RLIMIT_FSIZE, OUTPUT_BYTES),
        (resource.RLIMIT_NOFILE, OPEN_FILES),
        (resource.RLIMIT_CORE, 0),
        # No new processes or threads: a hijacked worker cannot leave children behind.
        (resource.RLIMIT_NPROC, 0),
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


def preload() -> None:
    """Import now everything rendering needs: after confine(), nothing can be loaded."""
    import PIL.Image
    import PIL.WebPImagePlugin
    import pypdfium2
    import pypdfium2.raw

    PIL.Image.init()
    del pypdfium2


def confine() -> str:  # pragma: no cover - runs only inside the worker process
    """Drop all filesystem and network access, except reading fonts (Linux Landlock).

    Returns how the worker is confined, for tests and `snug doctor`. Coverage
    cannot see into the worker process; tests/test_render_worker.py proves the
    confinement on Linux, where CI runs.
    """
    if not _is_linux():
        return "unconfined: not Linux"
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    create_ruleset, add_rule, restrict_self = 444, 445, 446  # same on every architecture

    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        return "unconfined: no_new_privs refused"
    abi = syscall(create_ruleset, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    if abi < 1:
        return "unconfined: Landlock unavailable"

    handled_fs = (1 << 13) - 1  # ABI 1: execute, write, read, make and remove everything
    if abi >= 2:
        handled_fs |= 1 << 13  # refer
    if abi >= 3:
        handled_fs |= 1 << 14  # truncate
    if abi >= 5:
        handled_fs |= 1 << 15  # ioctl on devices
    handled_net = 0b11 if abi >= 4 else 0  # bind and connect TCP
    scoped = 0b11 if abi >= 6 else 0  # abstract Unix sockets and signals

    class RulesetAttr(ctypes.Structure):
        _fields_ = (
            ("handled_access_fs", ctypes.c_uint64),
            ("handled_access_net", ctypes.c_uint64),
            ("scoped", ctypes.c_uint64),
        )

    class PathBeneathAttr(ctypes.Structure):
        _pack_ = 1
        _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))

    attr = RulesetAttr(handled_fs, handled_net, scoped)
    size = 8 if abi < 4 else 16 if abi < 6 else 24
    ruleset = syscall(create_ruleset, ctypes.byref(attr), ctypes.c_size_t(size), 0)
    if ruleset < 0:
        return "unconfined: ruleset refused"

    read_only = (1 << 2) | (1 << 3)  # read files, read directories
    for folder in FONT_DIRS:
        if os.path.isdir(folder):  # noqa: PTH112 - os.path only: pathlib may lazy-import
            o_path = getattr(os, "O_PATH", 0o10000000)  # Linux only
            fd = os.open(folder, o_path | os.O_CLOEXEC)
            rule = PathBeneathAttr(read_only, fd)
            syscall(add_rule, ruleset, 1, ctypes.byref(rule), 0)  # LANDLOCK_RULE_PATH_BENEATH
            os.close(fd)

    restricted = syscall(restrict_self, ruleset, 0)
    os.close(ruleset)
    if restricted != 0:
        return "unconfined: restrict_self refused"
    return f"landlock v{abi}"


def _fail(code: str) -> int:
    sys.stdout.write(json.dumps({"error": code}) + "\n")
    return 3


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _open(data: bytes) -> Any:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        if error.err_code in (pdfium_c.FPDF_ERR_PASSWORD, pdfium_c.FPDF_ERR_SECURITY):
            raise _Refused("password") from error
        raise _Refused("unreadable") from error


def inspect(data: bytes) -> dict[str, Any]:
    pdf = _open(data)
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


def render(data: bytes, page: int, width: int) -> bytes:
    pdf = _open(data)
    if not 1 <= page <= len(pdf):
        raise _Refused("no_such_page")
    pdf_page = pdf[page - 1]
    page_width, page_height = pdf_page.get_size()
    scale = width / page_width
    if (page_width * scale) * (page_height * scale) > MAX_PIXELS:
        scale = (MAX_PIXELS / (page_width * page_height)) ** 0.5
    scale = min(scale, MAX_SIDE / max(page_width, page_height))
    image = pdf_page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
    out = io.BytesIO()
    image.save(out, "WEBP", quality=85, method=4)
    return out.getvalue()


def main(argv: list[str], *, confined: bool = True) -> int:
    if confined:
        apply_limits()
    preload()
    try:
        page = width = 0
        if len(argv) == 2 and argv[0] == "inspect":
            mode = "inspect"
        elif len(argv) == 4 and argv[0] == "render":
            mode, page, width = "render", int(argv[2]), int(argv[3])
        else:
            return _fail("usage")
        with os.fdopen(int(argv[1]), "rb") as source:
            data = source.read()
        if confined:
            confine()
        if mode == "inspect":
            sys.stdout.write(json.dumps(inspect(data)) + "\n")
        else:
            sys.stdout.flush()
            sys.stdout.buffer.write(render(data, page, width))
    except _Refused as refused:
        return _fail(refused.code)
    except MemoryError:
        return _fail("memory")
    except Exception:  # noqa: BLE001 - any failure on hostile input is "unreadable"
        return _fail("unreadable")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
