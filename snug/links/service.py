"""Creating, reissuing and revoking links, and emailing them."""

from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from snug.accounts.models import Admin
from snug.core import mail
from snug.documents.models import Document
from snug.links.models import Link, new_ref

VIEWER_PREFIX = "/v/"


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
    document: Document,
    email: str,
    note: str,
    expires_at: datetime | None,
    require_verification: bool,
    allow_download: bool,
    created_by: Admin | None,
) -> Issued:
    for _ in range(5):
        link = Link(
            ref=new_ref(),
            email=email,
            note=note,
            document=document,
            expires_at=expires_at,
            require_verification=require_verification,
            allow_download=allow_download,
            created_by=created_by,
        )
        token = link.set_new_token()
        try:
            with transaction.atomic():
                link.save()
        except IntegrityError:
            continue  # a reference collision: 31^4 is small enough to retry
        return Issued(link=link, token=token)
    raise RuntimeError("Could not allocate a link reference.")


def reissue(link: Link) -> Issued:
    """A new token for the same link. The old URL stops working at once."""
    token = link.set_new_token()
    link.reissued_at = timezone.now()
    link.save(update_fields=["token_hash", "reissued_at"])
    return Issued(link=link, token=token)


def revoke(link: Link, by: Admin) -> None:
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.revoked_by = by
        link.save(update_fields=["revoked_at", "revoked_by"])


def send_link_email(issued: Issued, sender: Admin) -> bool:
    company = settings.CONFIG.company_name
    context = {
        "company": company,
        "document": issued.link.document,
        "url": issued.url,
        "link": issued.link,
        "sender": sender.email,
    }
    subject = (
        f"{company} has shared {issued.link.document.title} with you"
        if company
        else f"{issued.link.document.title} has been shared with you"
    )
    return mail.send(
        to=issued.link.email,
        subject=subject,
        text=render_to_string("links/email/link.txt", context),
        html=render_to_string("links/email/link.html", context),
        reply_to=sender.email,
    )
