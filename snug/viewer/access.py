"""The single authorisation path for viewer content.

Every viewer request for content calls authorise(), which checks, in order:

1. the link exists, is not revoked and has not expired;
2. this browser has a session bound to this link and this email address,
   verified if the link requires verification;
3. the current NDA has been accepted on this link, if the link or its room
   requires it;
4. the visitor is in the UK, if the link or its room is UK-only;
5. the requested document belongs to the link's document, or to its room.

A link that fails the first check, or a document outside the link, is
Unavailable: the viewer sees one page for unknown, expired and revoked links
alike, and learns nothing about which it was. A missing session is
NotVerified, and the viewer is sent to verify.
"""

from dataclasses import dataclass

from django.http import HttpRequest

from snug.core import geoip
from snug.core.client import client_ip
from snug.documents.models import Document
from snug.links.models import Link
from snug.nda import terms
from snug.rooms.models import RoomDocument
from snug.viewer import sessions
from snug.viewer.models import ViewerSession


class Denied(Exception):
    """The request may not see viewer content."""


class Unavailable(Denied):
    """No such link, or it is revoked or expired, or the document is not in it."""


class NotVerified(Denied):
    """The link is fine, but this browser has no session for it."""

    def __init__(self, link: Link) -> None:
        super().__init__(link.label)
        self.link = link


class OutsideUK(Denied):
    """The link is UK-only, and this visitor is elsewhere, or nobody can tell where."""

    def __init__(self, link: Link, country: str | None) -> None:
        super().__init__(link.label)
        self.link = link
        self.country = country


class NeedsNda(Denied):
    """The session is fine, but the NDA must be accepted on this link first."""

    def __init__(self, link: Link) -> None:
        super().__init__(link.label)
        self.link = link


@dataclass(frozen=True)
class Access:
    link: Link
    session: ViewerSession
    # The document requested. None for a room link's index, which lists the room.
    document: Document | None


# The UK for UK-only links: Great Britain and Northern Ireland, and the Crown
# Dependencies, which DB-IP lists apart (decided 6 November 2026).
UK = frozenset({"GB", "JE", "GG", "IM"})


def uk_only(link: Link) -> bool:
    """Whether this link opens only from the UK: its room's setting, or its own."""
    return link.room.uk_only if link.room is not None else link.uk_only


def check_place(request: HttpRequest, link: Link) -> None:
    """Check 4. Fails closed: an unknown country, or no data, is outside."""
    if not uk_only(link):
        return
    country = geoip.country(client_ip(request) or "")
    if country not in UK:
        raise OutsideUK(link, country)


def find_link(token: str) -> Link:
    """Check 1: the link exists and is active. Gate pages use this, then check_place."""
    link = Link.find(token)
    if link is None or not link.is_active():
        raise Unavailable
    return link


def authorise(
    request: HttpRequest,
    token: str,
    *,
    document_token: str | None = None,
    check_nda: bool = True,
) -> Access:
    """Allow a request for viewer content, or raise Denied. See the module docstring.

    Only the NDA page itself passes check_nda=False: it is where the NDA is
    accepted, so it cannot require that it already has been.
    """
    link = find_link(token)

    session = sessions.lookup(request, link)
    if (
        session is None
        or session.email != link.email
        or (link.require_verification and not session.verified)
    ):
        raise NotVerified(link)

    if check_nda and terms.required(link):
        version = terms.current()
        if version is not None and not terms.accepted(link, version):
            raise NeedsNda(link)

    check_place(request, link)

    # Check 5: the document belongs to the link's document, or to its room.
    # Room membership is looked up on every request, so removing a document
    # from a room ends access to it at once.
    if link.room is None:
        document = link.document
        if document is None or (document_token is not None and document_token != document.token):
            raise Unavailable
        return Access(link=link, session=session, document=document)
    if document_token is None:
        return Access(link=link, session=session, document=None)
    entry = (
        RoomDocument.objects.select_related("document")
        .filter(room=link.room, document__token=document_token)
        .first()
    )
    if entry is None:
        raise Unavailable
    return Access(link=link, session=session, document=entry.document)
