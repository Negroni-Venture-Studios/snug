"""Viewer sessions: a random token in a cookie, bound to a link and an email.

A session is also bound to the link's token at the time, so reissuing the
link ends every session for it.

These are separate from the admin's Django session. The cookie holds 256
random bits and the database keeps only their SHA-256 hash, so a copy of the
database or a backup opens nothing. The cookie is SameSite=Lax, not Strict,
so a viewer arriving from a link in an email is still recognised.
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from snug.links.models import Link
from snug.viewer.models import ViewerSession

COOKIE = f"{'__Host-' if settings.CONFIG.https else ''}snug-viewer"
_MAX_TOKEN = 100


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cookie_token(request: HttpRequest) -> str | None:
    token = request.COOKIES.get(COOKIE, "")
    return token if 0 < len(token) <= _MAX_TOKEN else None


def lookup(request: HttpRequest, link: Link) -> ViewerSession | None:
    """This browser's unexpired session for this link, or None."""
    token = _cookie_token(request)
    if token is None:
        return None
    return ViewerSession.objects.filter(
        token_hash=_hash(token),
        link=link,
        link_token_hash=link.token_hash,
        expires_at__gt=timezone.now(),
    ).first()


def begin(request: HttpRequest, response: HttpResponse, link: Link, *, verified: bool) -> None:
    """Give this browser a session for this link, and a fresh cookie.

    The cookie is rotated each time: the browser's sessions for other links
    move to the new token, and the old token stops working.
    """
    days = settings.CONFIG.viewer_session_days
    token = secrets.token_urlsafe(32)
    new_hash = _hash(token)
    with transaction.atomic():
        old = _cookie_token(request)
        if old is not None:
            ViewerSession.objects.filter(token_hash=_hash(old)).exclude(link=link).update(
                token_hash=new_hash
            )
            ViewerSession.objects.filter(token_hash=_hash(old), link=link).delete()
        ViewerSession.objects.create(
            token_hash=new_hash,
            link=link,
            link_token_hash=link.token_hash,
            email=link.email,
            verified=verified,
            expires_at=timezone.now() + timedelta(days=days),
        )
    response.set_cookie(
        COOKIE,
        token,
        max_age=days * 24 * 60 * 60,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )
