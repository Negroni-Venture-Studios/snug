"""The single authorisation path for viewer content.

Every viewer request for content calls authorise(), which checks, in order:

1. the link exists, is not revoked and has not expired;
2. this browser has a session bound to this link and this email address,
   verified if the link requires verification;
3. the NDA has been accepted, if one is required (rooms and NDAs: step 6);
4. the geographic restriction is met, if one is set (step 8);
5. the requested document belongs to the link's document (or room, step 6).

A link that fails the first check, or a document outside the link, is
Unavailable: the viewer sees one page for unknown, expired and revoked links
alike, and learns nothing about which it was. A missing session is
NotVerified, and the viewer is sent to verify.
"""

from dataclasses import dataclass

from django.http import HttpRequest

from snug.documents.models import Document
from snug.links.models import Link
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


@dataclass(frozen=True)
class Access:
    link: Link
    session: ViewerSession
    document: Document


def find_link(token: str) -> Link:
    """Check 1: the link exists and is active. Gate pages use this alone."""
    link = Link.find(token)
    if link is None or not link.is_active():
        raise Unavailable
    return link


def authorise(request: HttpRequest, token: str, *, document_token: str | None = None) -> Access:
    """Allow a request for viewer content, or raise Denied. See the module docstring."""
    link = find_link(token)

    session = sessions.lookup(request, link)
    if (
        session is None
        or session.email != link.email
        or (link.require_verification and not session.verified)
    ):
        raise NotVerified(link)

    if document_token is not None and document_token != link.document.token:
        raise Unavailable
    return Access(link=link, session=session, document=link.document)
