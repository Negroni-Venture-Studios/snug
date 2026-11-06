"""The founder's logo and tab icon: where they are kept, and which icon investors get.

<data>/branding/logo-<fingerprint>.png
<data>/branding/icon-<fingerprint>.png

Only a PNG that Snug drew itself is ever stored: the upload is decoded and
re-encoded by the confined worker first (snug.rendering.sandbox). The name
is a fingerprint of the stored bytes, so a new picture gets a new address and
browsers may cache each one for good.
"""

import hashlib
import re
import secrets
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from snug.rendering import sandbox

MAX_BYTES = 2 * 1024 * 1024
NAME = re.compile(r"^(logo|icon)-[0-9a-f]{16}\.png$")
# A logo this close to square serves as the tab icon too.
SQUARE_ENOUGH = 1.25

Kind = Literal["logo", "icon"]


def folder() -> Path:
    data_dir: Path = settings.CONFIG.data_dir
    return data_dir / "branding"


def path(name: str) -> Path:
    """A stored picture's path. `name` must be one Snug generated."""
    if not NAME.match(name):
        raise ValueError("Not a generated picture name.")
    return folder() / name


def draw(upload: "UploadedFile[bytes]", kind: Kind) -> bytes:
    """The upload, re-encoded as PNG by the confined worker. Raises sandbox.WorkerError."""
    data_dir: Path = settings.CONFIG.data_dir
    incoming = data_dir / "tmp" / f"{kind}-{secrets.token_hex(16)}"
    try:
        with incoming.open("xb") as out:
            for chunk in upload.chunks():
                out.write(chunk)
        return sandbox.logo(incoming) if kind == "logo" else sandbox.icon(incoming)
    finally:
        incoming.unlink(missing_ok=True)


def store(drawn: bytes, kind: Kind) -> str:
    """Keep a re-encoded picture. Returns its name."""
    name = f"{kind}-{hashlib.sha256(drawn).hexdigest()[:16]}.png"
    folder().mkdir(mode=0o700, exist_ok=True)
    partial = folder() / f".{name}.{secrets.token_hex(4)}"
    partial.write_bytes(drawn)
    partial.replace(path(name))
    return name


def discard(name: str) -> None:
    if name:
        path(name).unlink(missing_ok=True)


def size(name: str) -> tuple[int, int] | None:
    """A stored PNG's width and height, from its header, or None if it cannot be read."""
    try:
        with path(name).open("rb") as stored:
            header = stored.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def initial(company: str) -> str:
    """The first letter or digit of the company's name, in capitals, or ""."""
    return next((c.upper() for c in company if c.isalnum()), "")


def initial_version(company: str, accent: str) -> str:
    return hashlib.sha256(f"{initial(company)}|{accent}".encode()).hexdigest()[:10]


def initial_svg(company: str, fill: str, ink: str) -> str:
    """A rounded square in the accent colour, with the company's initial on it."""
    letter = initial(company)
    text = (
        f'<text x="32" y="44" text-anchor="middle" font-family="system-ui, sans-serif" '
        f'font-size="34" font-weight="600" fill="{ink}">{escape(letter)}</text>'
        if letter
        else ""
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="14" fill="{fill}"/>{text}</svg>\n'
    )


def favicon(logo_name: str, icon_name: str, company: str, accent: str) -> tuple[str, str]:
    """The address and type of investors' tab icon.

    An uploaded icon, else a logo near enough square, else the initial tile.
    """
    if icon_name:
        return f"/brand/{icon_name}", "image/png"
    dimensions = size(logo_name) if logo_name else None
    if dimensions and min(dimensions) and max(dimensions) / min(dimensions) <= SQUARE_ENOUGH:
        return f"/brand/{logo_name}", "image/png"
    return f"/brand/initial.svg?v={initial_version(company, accent)}", "image/svg+xml"
