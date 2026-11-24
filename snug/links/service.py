"""Creating, reissuing, extending and revoking links, and emailing them."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from snug.accounts.models import Admin
from snug.core import mail
from snug.core.models import company_name
from snug.documents import storage
from snug.documents.models import Document
from snug.links.models import Link, LinkPerson, is_domain, new_ref

if TYPE_CHECKING:
    from snug.rooms.models import Room

VIEWER_PREFIX = "/v/"
# A reference is four characters from 31, so 923,521 in all. A new one
# collides more often as links accumulate: a run of 50 collisions means the
# references are close to used up.
REF_ATTEMPTS = 50


class NoFreeReferenceError(Exception):
    """Every reference tried for a new link was taken."""


def link_url(token: str) -> str:
    return f"{settings.CONFIG.origin}{VIEWER_PREFIX}{token}"


@dataclass(frozen=True)
class Issued:
    link: Link
    token: str  # opens the link: show once, never store

    @property
    def url(self) -> str:
        return link_url(self.token)


def create(
    *,
    document: Document | None = None,
    room: "Room | None" = None,
    email: str = "",
    entries: "list[str] | None" = None,
    note: str,
    message: str = "",
    expires_at: datetime | None,
    require_verification: bool,
    require_nda: bool = False,
    allow_download: bool,
    uk_only: bool = False,
    created_by: Admin | None,
) -> Issued:
    if (document is None) == (room is None):
        raise ValueError("A link shares one document or one room.")
    listed = [entry.strip().casefold() for entry in (entries or [email]) if entry.strip()]
    if not listed:
        raise ValueError("A link is for at least one address or domain.")
    if (len(listed) > 1 or is_domain(listed[0])) and not require_verification:
        # Without a code, Snug could not tell who on the list is reading.
        raise ValueError("Only a link for one address can skip verification.")
    for _ in range(REF_ATTEMPTS):
        link = Link(
            ref=new_ref(),
            entries="\n".join(dict.fromkeys(listed)),
            note=note,
            message=message,
            document=document,
            room=room,
            expires_at=expires_at,
            require_verification=require_verification,
            require_nda=require_nda and room is None,
            allow_download=allow_download,
            uk_only=uk_only and room is None,
            created_by=created_by,
        )
        token = link.set_new_token()
        try:
            with transaction.atomic():
                link.save()
        except IntegrityError:
            continue  # a reference collision: try another
        return Issued(link=link, token=token)
    raise NoFreeReferenceError


def reissue(link: Link) -> Issued:
    """A new token for the same link. The old URL stops working at once."""
    token = link.set_new_token()
    link.reissued_at = timezone.now()
    link.save(update_fields=["token_hash", "reissued_at"])
    return Issued(link=link, token=token)


def extend(link: Link, expires_at: datetime | None) -> None:
    """A later expiry, or none, for the same link. Its URL keeps working."""
    link.expires_at = expires_at
    link.save(update_fields=["expires_at"])


def block(link: Link, address: str, by: Admin | None) -> bool:
    """Stop one person on a link. Returns True if they were not blocked already.

    Their sessions and code on this link end with it, so unblocking them later
    needs a new code (security audit of 1.4.0, F13). Done again for someone
    already blocked, it tidies anything left.
    """
    from snug.viewer.models import VerificationCode, ViewerSession

    with transaction.atomic():
        person, _ = LinkPerson.objects.select_for_update().get_or_create(link=link, email=address)
        newly = person.blocked_at is None
        if newly:
            person.blocked_at = timezone.now()
            person.blocked_by = by
            person.save(update_fields=["blocked_at", "blocked_by"])
        ViewerSession.objects.filter(link=link, email=address).delete()
        VerificationCode.objects.filter(link=link, email=address).delete()
    return newly


def revoke(link: Link, by: Admin | None) -> None:
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.revoked_by = by
        link.save(update_fields=["revoked_at", "revoked_by"])
    # Its investor's stamped pages: authorise() refuses them already, so this is tidying.
    storage.clear_stamped(link_id=int(link.pk))


def send_link_email(issued: Issued, sender: Admin) -> bool:
    company = company_name()
    context = {
        "company": company,
        "document": issued.link.document,
        "title": issued.link.target_title,
        "url": issued.url,
        "link": issued.link,
        "sender": sender.email,
    }
    subject = (
        f"{company} has shared {issued.link.target_title} with you"
        if company
        else f"{issued.link.target_title} has been shared with you"
    )
    # To each address the link lists. A domain has no one address to send
    # to, so the founder sends the link themselves.
    addresses = [entry for entry in issued.link.entry_list if not is_domain(entry)]
    sent = [_send_link(address, sender, subject, context) for address in addresses]
    return bool(sent) and all(sent)


def _send_link(to: str, sender: Admin, subject: str, context: Mapping[str, object]) -> bool:
    return mail.send(
        to=to,
        subject=subject,
        text=render_to_string("links/email/link.txt", context),
        html=render_to_string("links/email/link.html", context),
        reply_to=sender.email,
    )
