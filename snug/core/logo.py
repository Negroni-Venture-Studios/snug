"""The founder's logo: where it is kept, and how it gets there.

<data>/branding/logo-<fingerprint>.png

Only a PNG that Snug drew itself is ever stored: the upload is decoded and
re-encoded by the confined worker first (snug.rendering.sandbox.logo). The
name is a fingerprint of the stored bytes, so a new logo gets a new address
and browsers may cache each one for good.
"""

import hashlib
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from snug.rendering import sandbox

MAX_BYTES = 2 * 1024 * 1024
NAME = re.compile(r"^logo-[0-9a-f]{16}\.png$")


def folder() -> Path:
    data_dir: Path = settings.CONFIG.data_dir
    return data_dir / "branding"


def path(name: str) -> Path:
    """The stored logo's path. `name` must be one Snug generated."""
    if not NAME.match(name):
        raise ValueError("Not a generated logo name.")
    return folder() / name


def draw(upload: "UploadedFile[bytes]") -> bytes:
    """The upload, re-encoded as PNG by the confined worker. Raises sandbox.WorkerError."""
    data_dir: Path = settings.CONFIG.data_dir
    incoming = data_dir / "tmp" / f"logo-{secrets.token_hex(16)}"
    try:
        with incoming.open("xb") as out:
            for chunk in upload.chunks():
                out.write(chunk)
        return sandbox.logo(incoming)
    finally:
        incoming.unlink(missing_ok=True)


def store(drawn: bytes) -> str:
    """Keep a re-encoded logo. Returns its name."""
    name = f"logo-{hashlib.sha256(drawn).hexdigest()[:16]}.png"
    folder().mkdir(mode=0o700, exist_ok=True)
    partial = folder() / f".{name}.{secrets.token_hex(4)}"
    partial.write_bytes(drawn)
    partial.replace(path(name))
    return name


def discard(name: str) -> None:
    if name:
        path(name).unlink(missing_ok=True)
