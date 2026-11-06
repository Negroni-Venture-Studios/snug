"""A green notice after an admin save, shown once, in the section it was made in.

The view that saves calls `saved`, which keeps the notice in the admin's
session and redirects to the section's anchor. The page that renders next
calls `take`, which removes it, so a reload does not repeat it.
"""

from django.http import HttpRequest, HttpResponseRedirect

SESSION_KEY = "snug.notice"


def saved(request: HttpRequest, url: str, section: str, text: str) -> HttpResponseRedirect:
    request.session[SESSION_KEY] = {"section": section, "text": text}
    return HttpResponseRedirect(f"{url}#{section}")


def take(request: HttpRequest) -> dict[str, str] | None:
    notice = request.session.pop(SESSION_KEY, None)
    return notice if isinstance(notice, dict) else None
