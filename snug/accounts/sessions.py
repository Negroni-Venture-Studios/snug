"""Session state for signing in, and the idle and absolute time limits."""

import time
from collections.abc import Callable
from typing import TypedDict

from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse

PENDING = "snug.pending"
LOGIN_AT = "snug.login_at"
LAST_ACTIVITY = "snug.last_activity"

# After the password, the code must follow within five minutes.
PENDING_SECONDS = 5 * 60
# An admin session ends after an hour without activity, and after twelve
# hours whatever happens.
IDLE_SECONDS = 60 * 60
ABSOLUTE_SECONDS = 12 * 60 * 60
# Only rewrite the session for activity once a minute.
ACTIVITY_GRANULARITY = 60


class Pending(TypedDict):
    admin_id: int
    at: float
    failures: int
    next: str


def start_pending(request: HttpRequest, admin_id: int, next_url: str) -> None:
    # A fresh session key at each step, so a session id planted before the
    # password cannot be carried into the signed-in session.
    request.session.cycle_key()
    request.session[PENDING] = Pending(admin_id=admin_id, at=time.time(), failures=0, next=next_url)


def get_pending(request: HttpRequest) -> Pending | None:
    pending: Pending | None = request.session.get(PENDING)
    if pending is None:
        return None
    if time.time() - pending["at"] > PENDING_SECONDS:
        clear_pending(request)
        return None
    return pending


def record_code_failure(request: HttpRequest, pending: Pending) -> int:
    pending["failures"] += 1
    request.session[PENDING] = pending
    return pending["failures"]


def clear_pending(request: HttpRequest) -> None:
    request.session.pop(PENDING, None)


def mark_signed_in(request: HttpRequest) -> None:
    now = time.time()
    request.session[LOGIN_AT] = now
    request.session[LAST_ACTIVITY] = now


def session_limits(
    get_response: Callable[[HttpRequest], HttpResponse],
) -> Callable[..., HttpResponse]:
    """End admin sessions that have been idle, or open, too long."""

    def middleware(request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated:
            now = time.time()
            login_at = request.session.get(LOGIN_AT, 0)
            last = request.session.get(LAST_ACTIVITY, 0)
            if now - login_at > ABSOLUTE_SECONDS or now - last > IDLE_SECONDS:
                logout(request)
            elif now - last > ACTIVITY_GRANULARITY:
                request.session[LAST_ACTIVITY] = now
        return get_response(request)

    return middleware
