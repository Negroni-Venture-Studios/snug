"""Publishing the NDA, checking acceptance, and recording it.

One NDA for the instance, versioned (decided 5 November 2026). A room, or a
link to a single document, requires it or not. Acceptance is per link and per
version: publishing a new version asks everyone to accept again.
"""

import hashlib

from snug.accounts.models import Admin
from snug.links.models import Link
from snug.nda.models import NdaAcceptance, NdaVersion

MAX_TEXT = 50_000


def current() -> NdaVersion | None:
    return NdaVersion.objects.order_by("-number").first()


def publish(text: str, *, by: Admin | None) -> NdaVersion:
    """Make this the NDA. Unchanged text keeps the current version."""
    text = text.strip()
    latest = current()
    if latest is not None and latest.text == text:
        return latest
    return NdaVersion.objects.create(
        number=(latest.number if latest else 0) + 1,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_by=by,
    )


def required(link: Link) -> bool:
    """Whether this link needs the NDA: its room's setting, or its own for a document."""
    return link.room.require_nda if link.room is not None else link.require_nda


def accepted(link: Link, version: NdaVersion) -> bool:
    return NdaAcceptance.objects.filter(link=link, version=version).exists()


def accept(
    link: Link, version: NdaVersion, *, name: str, email: str, ip: str | None, user_agent: str
) -> NdaAcceptance:
    return NdaAcceptance.objects.create(
        link=link,
        version=version,
        name=name,
        email=email,
        ip_address=ip,
        user_agent=user_agent[:512],
        text_sha256=version.sha256,
    )
