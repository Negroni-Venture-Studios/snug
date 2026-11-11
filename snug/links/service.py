"""Creating, reissuing and revoking links, and emailing them."""

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
from snug.documents.models import Document
from snug.links.models import Link, new_ref

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
    email: str,
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
    for _ in range(REF_ATTEMPTS):
        link = Link(
            ref=new_ref(),
            email=email,
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


def revoke(link: Link, by: Admin | None) -> None:
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.revoked_by = by
        link.save(update_fields=["revoked_at", "revoked_by"])


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
    return mail.send(
        to=issued.link.email,
        subject=subject,
        text=render_to_string("links/email/link.txt", context),
        html=render_to_string("links/email/link.html", context),
        reply_to=sender.email,
    )
