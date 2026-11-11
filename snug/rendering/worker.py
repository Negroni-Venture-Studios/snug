"""The document worker: reads untrusted uploads in a separate, confined process.

    python -I -m snug.rendering.worker inspect FD
    python -I -m snug.rendering.worker render FD PAGE WIDTH
    python -I -m snug.rendering.worker stamp FD FONT_FD EMAIL DATE REF
    python -I -m snug.rendering.worker stamp-pdf FD FONT_FD EMAIL DATE REF
    python -I -m snug.rendering.worker inspect-xlsx FD
    python -I -m snug.rendering.worker stamp-xlsx FD EMAIL DATE REF
    python -I -m snug.rendering.worker inspect-mp4 FD
    python -I -m snug.rendering.worker logo FD
    python -I -m snug.rendering.worker icon FD
    python -I -m snug.rendering.worker confinement

Snug opens the file and passes the open file as descriptor FD; the worker is
never given a path. `inspect` prints one line of JSON. `render` writes the
WebP image to standard output. `stamp` reads a rendered page (as FD) and a
font, and writes the page with the viewer's watermark: a page rendered from
an untrusted PDF is never decoded outside a confined worker. `stamp-pdf`
writes the original PDF with the viewer's diagonal mark and a footer line on
every page, and the same details in its document properties. `inspect-xlsx`
and `stamp-xlsx` do the same for an Excel workbook (see
snug.rendering.workbook): the sheet names, and the workbook with the reader's
details in its properties. `inspect-mp4` checks an MP4 video's boxes,
reading them in pieces from the descriptor rather than whole (see
snug.rendering.video), and prints its length and size; the video itself is
never decoded. `logo` and `icon` redraw a branding image as a PNG.
`confinement` reads nothing: it confines itself and reports how, for
`snug doctor`. Errors print {"error": ...} and exit with 3.

Before it parses the file, the worker
  - limits its own memory, CPU time, output, open files and processes,
  - imports everything it will need,
  - reads the file's bytes from the descriptor (a video only once confined,
    in pieces), and
  - confines itself with Landlock, so it cannot open any file except system
    fonts (read-only) or make TCP connections, and a seccomp filter that
    refuses to create any socket, or to reach into another process: to
    trace it, signal it, read its memory, or change its priority or limits.
    Without both, or without its memory limit, it refuses to parse anything.
It imports no Django and no Snug settings, and its environment is empty of
secrets, so even a file that took over the code reading it would find nothing to take.
"""

import codecs
import io
import json
import os
import re
import struct
import sys
import zipfile
from typing import Any

from snug.rendering import video, workbook  # standard library only, like this module

MEMORY_BYTES = 1024 * 1024 * 1024  # 1 GiB of address space
CPU_SECONDS = 60
OUTPUT_BYTES = 64 * 1024 * 1024
OPEN_FILES = 32
MAX_PAGES = 2000
# A rendered page never exceeds this many pixels, or this length of side,
# however odd its shape. (WebP cannot exceed 16,383 pixels a side.)
MAX_PIXELS = 1600 * 4000
MAX_SIDE = 8000
# The watermark is drawn on a square as wide as the page's diagonal, which
# holds no more than this many pixels however long and thin the page.
MAX_MARK_PIXELS = 2 * MAX_PIXELS
FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "/etc/fonts")
# Every text codec pypdf names. Python loads a codec's module the first time
# it is used, and once the worker is confined it cannot open that file, so
# preload() loads them all first. Found 6 November 2026 with UTF-16 metadata.
PDF_CODECS = (
    "utf-8",
    "utf-16",
    "utf-16-be",
    "utf-16-le",
    "latin-1",
    "cp1252",
    "charmap",
    "mac_roman",
    "iso8859_15",
    "ascii",
)
# Landlock ABI 4 (Linux 6.7) is the minimum: it adds network rules, after ABI 3
# made truncation a controlled right, so a worker cannot empty the database.
MIN_LANDLOCK_ABI = 4
# Passed first on the command line only when the operator has set
# SNUG_UNSAFE_UNCONFINED_RENDERING, for development on a machine without Landlock.
ALLOW_UNCONFINED = "--unsafe-unconfined"
_FULLY_CONFINED = re.compile(r"^landlock v(\d+), no sockets$")
MAX_STAMP_TEXT = 254
_REF_CHARACTERS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
# The watermark: a faint diagonal mark across the page, and a footer band.
MARK_ANGLE = 22
MARK_COLOUR = (96, 96, 96, 72)
FOOTER_BACKGROUND = (245, 245, 244)
FOOTER_TEXT = (87, 83, 78)


def _is_linux() -> bool:
    # A function, so type checkers on macOS and Linux both see every branch.
    return sys.platform == "linux"


def apply_limits() -> bool:
    """Limit the worker's resources. False if, on Linux, the memory limit could not be set.

    Without it main() refuses to read anything, as it does unconfined, unless
    the operator allowed that. macOS does not enforce an address-space limit,
    and setting one may fail there; the worker cannot confine itself there
    either, so it reads nothing without the operator's opt-in anyway.
    """
    import resource

    limits = [
        (resource.RLIMIT_CPU, CPU_SECONDS),
        (resource.RLIMIT_FSIZE, OUTPUT_BYTES),
        (resource.RLIMIT_NOFILE, OPEN_FILES),
        (resource.RLIMIT_CORE, 0),
        # No new processes or threads: a hijacked worker cannot leave children behind.
        (resource.RLIMIT_NPROC, 0),
    ]
    for kind, value in limits:
        resource.setrlimit(kind, (value, value))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    except (ValueError, OSError):
        return not _is_linux()
    return True


def preload() -> None:
    """Import now everything rendering needs: after confine(), nothing can be loaded."""
    import PIL.Image
    import PIL.ImageDraw
    import PIL.ImageFont
    import PIL.ImageOps
    import PIL.WebPImagePlugin
    import pypdfium2
    import pypdfium2.raw

    PIL.Image.init()
    del pypdfium2
    for name in PDF_CODECS:
        codecs.lookup(name)
    _warm_up_pypdf()
    _warm_up_workbook()


def _warm_up_workbook() -> None:
    """Read and stamp a tiny workbook, so zipfile, zlib and expat are loaded before confine()."""
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as parts:
        parts.writestr(
            workbook.CONTENT_TYPES,
            f'<Types xmlns="{workbook.NS_TYPES}"><Override PartName="/xl/workbook.xml" '
            f'ContentType="{workbook.MAIN}"/></Types>',
        )
        parts.writestr(
            workbook.ROOT_RELS,
            f'<Relationships xmlns="{workbook.NS_RELS}"><Relationship Id="rId1" '
            f'Type="{workbook.REL_DOC}" Target="xl/workbook.xml"/></Relationships>',
        )
        parts.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{workbook.NS_MAIN}"><sheets><sheet name="A"/></sheets></workbook>',
        )
    workbook.inspect(package.getvalue())
    workbook.stamp(package.getvalue(), email="warm@up.example", date="1 Jan 2026", ref="WARM")


def _warm_up_pypdf() -> None:
    """Exercise pypdf once, so every module it loads lazily is loaded before confine()."""
    blank = io.BytesIO()
    writer = __import__("pypdf").PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(blank)
    _stamp_pdf_pages(blank.getvalue(), None, "warm@up.example", "1 Jan 2026", "WARM")


def fully_confined(how: str) -> bool:
    """True if `confine` reported Landlock at the minimum ABI and the seccomp filter."""
    found = _FULLY_CONFINED.match(how)
    return found is not None and int(found.group(1)) >= MIN_LANDLOCK_ABI


def _may_read(*, limited: bool, allow_unconfined: bool) -> bool:
    """Confine the worker, then say whether it may read the file.

    Fail closed: a hostile file is never parsed outside the confinement, or
    without the memory limit, unless the operator asked for that on a
    development machine.
    """
    return (fully_confined(confine()) and limited) or allow_unconfined


# The syscalls the seccomp filter refuses outright. io_uring can open sockets
# without calling socket(), so it goes too. The rest reach into another
# process: tracing it, reading or writing its memory, signalling it through a
# pidfd, or changing its priority, scheduling or memory placement. The worker
# does none of these to itself either.
_REFUSED = (
    "socket",
    "io_uring_setup",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "pidfd_open",
    "pidfd_getfd",
    "pidfd_send_signal",
    "setpriority",
    "ioprio_set",
    "sched_setaffinity",
    "sched_setscheduler",
    "sched_setparam",
    "kcmp",
    "move_pages",
    "migrate_pages",
    "process_madvise",
    "sched_setattr",
    "process_mrelease",
)
# The syscalls that send a signal: allowed only to the worker itself.
_SIGNALLING = ("kill", "tkill", "tgkill", "rt_sigqueueinfo", "rt_tgsigqueueinfo")
# Each architecture's audit value and syscall numbers.
_SECCOMP_ARCHES = {
    "x86_64": (
        0xC000003E,
        {
            "socket": 41,
            "kill": 62,
            "ptrace": 101,
            "rt_sigqueueinfo": 129,
            "setpriority": 141,
            "sched_setparam": 142,
            "sched_setscheduler": 144,
            "tkill": 200,
            "sched_setaffinity": 203,
            "tgkill": 234,
            "ioprio_set": 251,
            "migrate_pages": 256,
            "move_pages": 279,
            "rt_tgsigqueueinfo": 297,
            "prlimit64": 302,
            "process_vm_readv": 310,
            "process_vm_writev": 311,
            "kcmp": 312,
            "pidfd_send_signal": 424,
            "io_uring_setup": 425,
            "pidfd_open": 434,
            "pidfd_getfd": 438,
            "process_madvise": 440,
            "sched_setattr": 314,
            "process_mrelease": 448,
        },
    ),
    "aarch64": (
        0xC00000B7,
        {
            "ioprio_set": 30,
            "ptrace": 117,
            "sched_setparam": 118,
            "sched_setscheduler": 119,
            "sched_setaffinity": 122,
            "kill": 129,
            "tkill": 130,
            "tgkill": 131,
            "rt_sigqueueinfo": 138,
            "setpriority": 140,
            "socket": 198,
            "migrate_pages": 238,
            "move_pages": 239,
            "rt_tgsigqueueinfo": 240,
            "prlimit64": 261,
            "process_vm_readv": 270,
            "process_vm_writev": 271,
            "kcmp": 272,
            "pidfd_send_signal": 424,
            "io_uring_setup": 425,
            "pidfd_open": 434,
            "pidfd_getfd": 438,
            "process_madvise": 440,
            "sched_setattr": 274,
            "process_mrelease": 448,
        },
    ),
}

# A BPF instruction: its code, its constant, and the labels it jumps to if
# its test is true or false (None for the next instruction).
_Instruction = tuple[int, int, str | None, str | None]


def _assemble(lines: list[str | _Instruction]) -> bytes:
    """BPF bytecode from instructions and the labels between them. Jumps go forward only."""
    labels: dict[str, int] = {}
    instructions: list[_Instruction] = []
    for line in lines:
        if isinstance(line, str):
            labels[line] = len(instructions)
        else:
            instructions.append(line)

    def offset(index: int, label: str | None) -> int:
        return 0 if label is None else labels[label] - index - 1

    return b"".join(
        struct.pack("HBBI", code, offset(index, true), offset(index, false), k)
        for index, (code, k, true, false) in enumerate(instructions)
    )


def _seccomp_filter(machine: str, pid: int) -> bytes | None:
    """A seccomp program for the worker, whose process ID is `pid`. Refused calls get EPERM.

    It refuses to create sockets: Landlock's network rules cover TCP only, so
    without this a compromised worker could still send UDP. It refuses every
    call in _REFUSED, so the worker cannot trace, re-prioritise or read the
    memory of another process. It lets the worker send a signal only to
    itself (abort() does), never to another process or process group, and
    change only its own limits. Landlock scopes signals only from ABI 6. Any
    other architecture or ABI (32-bit compatibility calls, x32) is killed
    outright rather than filtered.
    """
    if machine not in _SECCOMP_ARCHES:
        return None
    arch, numbers = _SECCOMP_ARCHES[machine]
    load, jump_eq, jump_ge, ret = 0x20, 0x15, 0x35, 0x06  # BPF_LD|W|ABS, JEQ, JGE, RET
    allow, eperm, kill = 0x7FFF0000, 0x00050000 | 1, 0x80000000
    # The target of a signal or of prlimit64: the low 32 bits of its first
    # argument, which is all of a pid_t.
    target = 16

    lines: list[str | _Instruction] = [
        (load, 4, None, None),  # the architecture
        (jump_eq, arch, None, "kill"),
        (load, 0, None, None),  # the syscall number
    ]
    if machine == "x86_64":
        lines.append((jump_ge, 0x40000000, "refuse", None))  # x32 calls carry this bit
    lines += [(jump_eq, numbers[name], "refuse", None) for name in _REFUSED]
    lines += [(jump_eq, numbers[name], "signal", None) for name in _SIGNALLING]
    lines += [
        (jump_eq, numbers["prlimit64"], "limits", None),
        (ret, allow, None, None),  # anything else
        "signal",
        (load, target, None, None),
        (jump_eq, pid, "allow", "refuse"),  # the worker itself; never 0, its process group
        "limits",
        (load, target, None, None),
        (jump_eq, 0, "allow", None),  # 0 is the caller: glibc's getrlimit and setrlimit
        (jump_eq, pid, "allow", "refuse"),
        "allow",
        (ret, allow, None, None),
        "refuse",
        (ret, eperm, None, None),
        "kill",
        (ret, kill, None, None),
    ]
    return _assemble(lines)


def confine() -> str:  # pragma: no cover - runs only inside the worker process
    """Drop all filesystem and network access, except reading fonts (Linux Landlock).

    Then a seccomp filter refuses to create sockets of any kind, or to trace,
    signal, re-prioritise or change the limits of another process (see
    _seccomp_filter). Returns how the worker is
    confined, for the caller (which refuses to go on unless `fully_confined`
    says so), tests and `snug doctor`. Coverage cannot see
    into the worker process; tests/test_render_worker.py proves the
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

    # Built before Landlock applies: afterwards not even Python's own files open.
    program = _seccomp_filter(os.uname().machine, os.getpid())

    class SockFprog(ctypes.Structure):
        _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))

    restricted = syscall(restrict_self, ruleset, 0)
    os.close(ruleset)
    if restricted != 0:
        return "unconfined: restrict_self refused"
    if program is None:
        return f"landlock v{abi}, socket filter unavailable"
    buffer = ctypes.create_string_buffer(program, len(program))
    fprog = SockFprog(len(program) // 8, ctypes.cast(buffer, ctypes.c_void_p))
    if libc.prctl(22, 2, ctypes.byref(fprog), 0, 0) != 0:  # PR_SET_SECCOMP, MODE_FILTER
        return f"landlock v{abi}, socket filter refused"
    return f"landlock v{abi}, no sockets"


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


def _stamp_text(email: str, date: str, ref: str) -> tuple[str, str, str]:
    for text in (email, date):
        if not text or len(text) > MAX_STAMP_TEXT or not text.isprintable():
            raise _Refused("usage")
    if not ref or len(ref) > 12 or not set(ref) <= _REF_CHARACTERS:
        raise _Refused("usage")
    return email, date, ref


def _mark_layer(width: int, height: int, font: bytes | None, email: str, date: str) -> Any:
    """The diagonal mark: rows of "email  date", tiled and rotated, on a clear layer.

    Drawn on a square big enough to cover the page at any angle, rotated, then
    cut to the page. A page whose square would hold more than MAX_MARK_PIXELS
    has its mark drawn smaller, then enlarged to the page. `font` None uses
    Pillow's built-in font (for the warm-up).
    """
    from PIL import Image, ImageDraw, ImageFont

    page = (width, height)
    shrink = min(1.0, (MAX_MARK_PIXELS / max(width * width + height * height, 1)) ** 0.5)
    width, height = max(1, round(width * shrink)), max(1, round(height * shrink))
    size = max(14, round(width * 0.018))
    mark_font = ImageFont.truetype(io.BytesIO(font), size) if font else ImageFont.load_default(size)
    side = int((width**2 + height**2) ** 0.5) + 1
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    text = f"{email}   {date}"
    step_x = int(draw.textlength(text, font=mark_font)) + size * 4
    step_y = size * 7
    for row, y in enumerate(range(0, side, step_y)):
        for x in range(-step_x + (row % 2) * step_x // 2, side, step_x):
            draw.text((x, y), text, font=mark_font, fill=MARK_COLOUR)
    layer = layer.rotate(MARK_ANGLE, resample=Image.Resampling.BICUBIC)
    left, top = (side - width) // 2, (side - height) // 2
    layer = layer.crop((left, top, left + width, top + height))
    return layer if layer.size == page else layer.resize(page, Image.Resampling.BICUBIC)


def stamp(data: bytes, font: bytes, email: str, date: str, ref: str) -> bytes:
    """The page with the viewer's watermark: a diagonal mark, and a footer band."""
    from PIL import Image, ImageDraw, ImageFont

    email, date, ref = _stamp_text(email, date, ref)
    with Image.open(io.BytesIO(data), formats=["WEBP"]) as source:
        width, height = source.size
        if max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
            raise _Refused("unreadable")
        page = source.convert("RGB")

    marked = Image.alpha_composite(
        page.convert("RGBA"), _mark_layer(width, height, font, email, date)
    )

    # The footer: who it was shared with, the date, and the link, in three columns.
    band = max(28, round(width * 0.03))
    out = Image.new("RGB", (width, height + band), FOOTER_BACKGROUND)
    out.paste(marked.convert("RGB"), (0, 0))
    foot_font = ImageFont.truetype(io.BytesIO(font), max(11, round(band * 0.42)))
    foot = ImageDraw.Draw(out)
    middle = height + band // 2
    margin = round(band * 0.8)
    foot.text(
        (margin, middle), f"Shared with {email}", font=foot_font, fill=FOOTER_TEXT, anchor="lm"
    )
    foot.text((width // 2, middle), date, font=foot_font, fill=FOOTER_TEXT, anchor="mm")
    foot.text(
        (width - margin, middle), f"Link {ref}", font=foot_font, fill=FOOTER_TEXT, anchor="rm"
    )

    result = io.BytesIO()
    out.save(result, "WEBP", quality=85, method=4)
    return result.getvalue()


# The founder's logo is drawn no larger than this, in pixels.
LOGO_BOX = (800, 240)
# The tab icon is a square this many pixels a side: enough for a phone's home screen.
ICON_SIDE = 180


def _picture(data: bytes) -> Any:
    """A PNG or JPEG, decoded to its pixels alone, upright, with any transparency kept.

    Nothing of the original file survives but its pixels: no metadata, and
    nothing hidden after the image data. The size limits are checked from the
    header, before any pixels are decoded.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        source = Image.open(io.BytesIO(data), formats=["PNG", "JPEG"])
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise _Refused("unreadable") from error
    with source:
        width, height = source.size
        if max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
            raise _Refused("unreadable")
        clear = source.mode in ("RGBA", "LA", "PA") or "transparency" in source.info
        image = ImageOps.exif_transpose(source).convert("RGBA" if clear else "RGB")
    image.info.clear()
    return image


def _png(image: Any) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def logo(data: bytes) -> bytes:
    """The founder's logo, drawn afresh as a PNG that fits LOGO_BOX."""
    from PIL import Image

    image = _picture(data)
    image.thumbnail(LOGO_BOX, Image.Resampling.LANCZOS)
    return _png(image)


def icon(data: bytes) -> bytes:
    """The founder's tab icon: the picture fitted, centred, on a clear ICON_SIDE square."""
    from PIL import Image

    image = _picture(data).convert("RGBA")
    image.thumbnail((ICON_SIDE, ICON_SIDE), Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (ICON_SIDE, ICON_SIDE), (0, 0, 0, 0))
    square.paste(image, ((ICON_SIDE - image.width) // 2, (ICON_SIDE - image.height) // 2))
    return _png(square)


# The PDF overlay is drawn at this many pixels per point, within MAX_PIXELS.
OVERLAY_DENSITY = 1.5


def _overlay_pdf(
    width_pt: float, height_pt: float, font: bytes | None, email: str, date: str
) -> bytes:
    """A one-page PDF holding the diagonal mark as a grey image with a transparency mask.

    An image, not text, so text copied from the download is not full of
    watermark strings. Pillow cannot write a transparent image to a PDF, so
    the few objects are written here.
    """
    import zlib

    # The diagonal is what is capped, as in _mark_layer, so the overlay holds
    # no more than MAX_PIXELS whatever the page's shape, and is never enlarged.
    diagonal_squared = max(width_pt * width_pt + height_pt * height_pt, 1)
    scale = min(OVERLAY_DENSITY, (MAX_MARK_PIXELS / diagonal_squared) ** 0.5)
    w, h = max(1, round(width_pt * scale)), max(1, round(height_pt * scale))
    alpha = _mark_layer(w, h, font, email, date).getchannel("A").tobytes()
    grey = bytes([MARK_COLOUR[0]]) * (w * h)

    def image(data: bytes, extra: bytes = b"") -> bytes:
        body = zlib.compress(data, 9)
        head = (
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d" % (w, h, len(body))
        )
        return head + extra + b" >>\nstream\n" + body + b"\nendstream"

    content = f"q {width_pt:.3f} 0 0 {height_pt:.3f} 0 0 cm /SnugMark Do Q".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width_pt:.3f} {height_pt:.3f}] "
        f"/Resources << /XObject << /SnugMark 4 0 R >> >> /Contents 6 0 R >>".encode(),
        image(grey, b" /SMask 5 0 R"),
        image(alpha),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _pdf_string(text: str) -> bytes:
    """Text as a PDF literal string in WinAnsi: brackets and backslashes escaped."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("cp1252", errors="replace") + b")"


def _stamp_pdf_pages(data: bytes, font: bytes | None, email: str, date: str, ref: str) -> bytes:
    from pypdf import PageObject, PdfReader, PdfWriter, Transformation
    from pypdf.generic import ContentStream, DictionaryObject, NameObject

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise _Refused("password")
    writer = PdfWriter(clone_from=reader)
    line = _pdf_string(f"Shared with {email}   {date}   Link {ref}")
    # Helvetica is one of the standard fonts every PDF reader has: nothing to
    # embed, and the line stays text that can be selected and searched.
    helvetica = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    overlays: dict[tuple[float, float], Any] = {}
    for page in writer.pages:
        page.transfer_rotation_to_content()
        box = page.cropbox
        size = (round(float(box.width), 2), round(float(box.height), 2))
        if size not in overlays:
            overlays[size] = PdfReader(io.BytesIO(_overlay_pdf(*size, font, email, date))).pages[0]
        page.merge_transformed_page(
            overlays[size], Transformation().translate(float(box.left), float(box.bottom))
        )
        overlay = PageObject.create_blank_page(
            width=float(page.mediabox.width), height=float(page.mediabox.height)
        )
        overlay[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/SnugMark"): helvetica})}
        )
        x, y = float(box.left) + 18, float(box.bottom) + 10
        content = ContentStream(None, None)
        content.set_data(
            b"q BT /SnugMark 7 Tf 0.34 0.33 0.31 rg "
            + f"{x:.2f} {y:.2f} Td ".encode()
            + line
            + b" Tj ET Q"
        )
        overlay.replace_contents(content)
        page.merge_page(overlay)
    writer.add_metadata(
        {
            "/Subject": f"Shared with {email} on {date}, link {ref}",
            "/SnugSharedWith": email,
            "/SnugDate": date,
            "/SnugLink": ref,
        }
    )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _resaved(data: bytes) -> bytes:
    """The PDF as PDFium writes it: a well-formed, unencrypted copy pypdf can read.

    Snug only ever holds PDFs that open without a password, so removing the
    encryption loses only the owner's restrictions; the stamped download is
    written afresh, without them, either way.
    """
    import pypdfium2.raw as pdfium_c

    out = io.BytesIO()
    _open(data).save(out, flags=pdfium_c.FPDF_REMOVE_SECURITY)
    return out.getvalue()


def stamp_pdf(data: bytes, font: bytes, email: str, date: str, ref: str) -> bytes:
    """The original PDF with a line naming the viewer on every page."""
    from pypdf.errors import DependencyError, PdfReadError

    email, date, ref = _stamp_text(email, date, ref)
    try:
        return _stamp_pdf_pages(data, font, email, date, ref)
    except (PdfReadError, DependencyError):
        # PDFium reads many slightly broken files that pypdf will not, and Snug
        # accepted this one because PDFium could. pypdf also cannot decrypt
        # AES without the `cryptography` library, which Snug does not carry:
        # a PDF that opens without a password but restricts copying is common.
        # Stamp PDFium's clean, unencrypted copy.
        return _stamp_pdf_pages(_resaved(data), font, email, date, ref)


def _inspect_mp4(descriptor: int, *, confined: bool, limited: bool, allow_unconfined: bool) -> int:
    """A video, checked from its descriptor in pieces: it may be larger than our memory."""
    with os.fdopen(descriptor, "rb") as source:
        size = os.fstat(source.fileno()).st_size
        if confined and not _may_read(limited=limited, allow_unconfined=allow_unconfined):
            return _fail("unconfined")
        try:
            facts = video.inspect(source, size)
        except video.Refused as refused:
            return _fail(refused.code)
        except MemoryError:
            return _fail("memory")
        except Exception:  # noqa: BLE001 - any failure on hostile input is "unreadable"
            return _fail("unreadable")
    sys.stdout.write(json.dumps(facts) + "\n")
    return 0


def main(argv: list[str], *, confined: bool = True) -> int:
    allow_unconfined = bool(argv) and argv[0] == ALLOW_UNCONFINED
    if allow_unconfined:
        argv = argv[1:]
    limited = apply_limits() if confined else True
    if argv == ["confinement"]:
        # For `snug doctor`: confine, say how, and read nothing.
        how = confine() if confined else "unconfined: not asked"
        sys.stdout.write((how if limited else "unconfined: memory limit refused") + "\n")
        return 0
    preload()
    try:
        page = width = 0
        font = b""
        if len(argv) == 2 and argv[0] == "inspect-mp4":
            return _inspect_mp4(
                int(argv[1]),
                confined=confined,
                limited=limited,
                allow_unconfined=allow_unconfined,
            )
        if len(argv) == 2 and argv[0] in ("inspect", "logo", "icon", "inspect-xlsx"):
            mode = argv[0]
        elif len(argv) == 5 and argv[0] == "stamp-xlsx":
            mode = "stamp-xlsx"
        elif len(argv) == 4 and argv[0] == "render":
            mode, page, width = "render", int(argv[2]), int(argv[3])
        elif len(argv) == 6 and argv[0] == "stamp":
            mode = "stamp"
            with os.fdopen(int(argv[2]), "rb") as font_file:
                font = font_file.read()
        elif len(argv) == 6 and argv[0] == "stamp-pdf":
            mode = "stamp-pdf"
            with os.fdopen(int(argv[2]), "rb") as font_file:
                font = font_file.read()
        else:
            return _fail("usage")
        with os.fdopen(int(argv[1]), "rb") as source:
            data = source.read()
        if confined and not _may_read(limited=limited, allow_unconfined=allow_unconfined):
            return _fail("unconfined")
        if mode == "inspect":
            sys.stdout.write(json.dumps(inspect(data)) + "\n")
        elif mode == "inspect-xlsx":
            sys.stdout.write(json.dumps(workbook.inspect(data)) + "\n")
        else:
            if mode == "stamp-xlsx":
                output = workbook.stamp(data, email=argv[2], date=argv[3], ref=argv[4])
            elif mode == "render":
                output = render(data, page, width)
            elif mode == "stamp":
                output = stamp(data, font, argv[3], argv[4], argv[5])
            elif mode == "logo":
                output = logo(data)
            elif mode == "icon":
                output = icon(data)
            else:
                output = stamp_pdf(data, font, argv[3], argv[4], argv[5])
            sys.stdout.flush()
            sys.stdout.buffer.write(output)
    except (_Refused, workbook.Refused) as refused:
        return _fail(refused.code)
    except MemoryError:
        return _fail("memory")
    except Exception:  # noqa: BLE001 - any failure on hostile input is "unreadable"
        return _fail("unreadable")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
