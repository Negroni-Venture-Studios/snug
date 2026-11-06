"""First-open emails: the first time a link shows anyone a document.

Every working admin is emailed, unless they have turned it off under Your
account in Settings. The email says who, what, when and where, with a link
to that person on the People page. It is sent in the background, so the
reader never waits for it.
"""

from datetime import datetime

from django.conf import settings
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils import timezone

from snug.accounts.models import Admin
from snug.analytics import summary
from snug.core import countries, geoip, mail
from snug.core.client import client_ip
from snug.core.models import company_name
from snug.documents.models import Document
from snug.links.models import Link
from snug.viewer.access import Access


def claim(link: Link) -> bool:
    """Mark the link opened. True for the one call that does; every later one is False.

    A single conditional update, so two pages loading together cannot both claim it.
    """
    claimed = Link.objects.filter(pk=link.pk, first_opened_at__isnull=True).update(
        first_opened_at=timezone.now()
    )
    return claimed == 1


def _when(moment: datetime) -> str:
    local = timezone.localtime(moment)
    return f"{local:%H:%M} on {local:%A} {local.day} {local:%B %Y}"


def first_open(request: HttpRequest, access: Access, document: Document) -> None:
    """Call when a visit begins. Emails the admins if this is the link's first open."""
    if not claim(access.link):
        return
    recipients = sorted(
        Admin.objects.filter(is_active=True, notify_first_open=True).values_list("email", flat=True)
    )
    if not recipients or not mail.enabled():
        return
    code = geoip.country(client_ip(request) or "")
    origin = settings.CONFIG.origin
    company = company_name()
    who = access.session.email
    context = {
        "company": company,
        "who": who,
        "note": access.link.note,
        "document": document.title,
        "room": access.link.room.name if access.link.room is not None else "",
        "when": _when(timezone.now()),
        "where": countries.name(code) if code else "",
        "ref": access.link.label,
        "person_url": f"{origin}/admin/people/#{summary.anchor(who)}",
        "settings_url": f"{origin}/admin/settings/#notifications",
    }
    subject = f"{company or 'Snug'}: {who} opened {document.title}"
    text = render_to_string("analytics/email/first_open.txt", context)
    html = render_to_string("analytics/email/first_open.html", context)

    def send() -> None:
        for email in recipients:
            mail.send(to=email, subject=subject, text=text, html=html)

    mail.in_background(send)
