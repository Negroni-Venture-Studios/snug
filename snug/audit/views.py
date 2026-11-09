"""The audit log in the admin: newest first, filterable, and exportable as CSV."""

import csv
import json
from collections.abc import Iterator
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record
from snug.core.client import client_ip, user_agent

PER_PAGE = 100
KINDS = {
    "admin": ("Admins", "admin."),
    "link": ("Links", "link."),
    "viewer": ("Viewers", "viewer."),
    "document": ("Documents", "document."),
    "room": ("Rooms", "room."),
    "settings": ("Settings", "settings."),
    "system": ("Housekeeping", "system."),
    "audit": ("Exports", "audit."),
}
FIELDS = (
    "time",
    "actor type",
    "actor",
    "action",
    "target",
    "ip address",
    "user agent",
    "country",
    "details",
)
# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _events(request: HttpRequest) -> tuple[QuerySet[AuditEvent], str, str]:
    events = AuditEvent.objects.all()
    kind = request.GET.get("kind", "")
    if kind in KINDS:
        events = events.filter(action__startswith=KINDS[kind][1])
    else:
        kind = ""
    query = request.GET.get("q", "").strip()[:254]
    if query:
        events = events.filter(Q(actor__icontains=query) | Q(target__icontains=query))
    return events, kind, query


@never_cache
@login_required
@require_safe
def audit_log(request: HttpRequest) -> HttpResponse:
    events, kind, query = _events(request)
    page = Paginator(events, PER_PAGE).get_page(request.GET.get("page"))
    context: dict[str, Any] = {
        "tab": "audit",
        "page": page,
        "kind": kind,
        "q": query,
        "kinds": [(key, label) for key, (label, _) in KINDS.items()],
    }
    return render(request, "audit/list.html", context)


def _safe(value: str) -> str:
    """Defuse a cell a spreadsheet would run as a formula."""
    return f"'{value}" if value.startswith(_FORMULA) else value


class _Echo:
    def write(self, value: str) -> str:
        return value


def _rows(events: QuerySet[AuditEvent]) -> Iterator[str]:
    writer = csv.writer(_Echo())
    yield writer.writerow(FIELDS)
    for event in events.iterator(chunk_size=500):
        yield writer.writerow(
            [
                _safe(value)
                for value in (
                    event.at.isoformat(),
                    event.actor_type,
                    event.actor,
                    event.action,
                    event.target,
                    event.ip_address or "",
                    event.user_agent,
                    event.country,
                    json.dumps(event.details, sort_keys=True),
                )
            ]
        )


@never_cache
@login_required
@require_safe
def audit_export(request: HttpRequest) -> StreamingHttpResponse:
    events, kind, query = _events(request)
    admin = request.user
    assert isinstance(admin, Admin)  # noqa: S101 - login_required
    record(
        "audit.exported",
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=admin.email,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details={"kind": kind, "q": query},
    )
    name = f"snug-audit-{timezone.localdate():%Y-%m-%d}.csv"
    response = StreamingHttpResponse(_rows(events), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response
