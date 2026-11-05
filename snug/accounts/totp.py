"""Time-based one-time passwords (RFC 6238), as authenticator apps make them."""

import hmac
import io
import re
from datetime import UTC, datetime

import pyotp
import segno

DIGITS = 6
STEP_SECONDS = 30
# Accept the current step and one either side, for clocks that drift.
WINDOW = 1
_CODE = re.compile(r"^[0-9]{6}$")  # ASCII only: \d also matches other scripts' digits


def new_secret() -> str:
    """A fresh 160-bit secret, base32-encoded, as RFC 4226 recommends."""
    return pyotp.random_base32(length=32)


def provisioning_uri(secret: str, email: str, domain: str) -> str:
    """The otpauth:// URI an authenticator app reads from the QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=f"Snug ({domain})")


def terminal_qr(uri: str) -> str:
    """The URI as a QR code drawn with block characters, for a terminal."""
    buffer = io.StringIO()
    segno.make(uri, error="m").terminal(out=buffer, compact=True)
    return buffer.getvalue()


def grouped(secret: str) -> str:
    """The secret in groups of four, for typing into an app by hand."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


def verify(secret: str, code: str, last_step: int, now: datetime | None = None) -> int | None:
    """Check a code. Returns the time step it belongs to, or None.

    A step at or before `last_step` is refused, so each code works only once,
    even inside its 30 seconds. Store the returned step as the new last step.
    """
    code = re.sub(r"\s", "", code)
    if not secret or not _CODE.match(code):
        return None
    totp = pyotp.TOTP(secret, digits=DIGITS, interval=STEP_SECONDS)
    now = now or datetime.now(tz=UTC)
    current = totp.timecode(now)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step > last_step and hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None
