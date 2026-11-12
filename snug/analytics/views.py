"""The admin's People page: everyone who has read anything, and what they read."""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from snug.analytics import charts, summary


@never_cache
@login_required
@require_safe
def people(request: HttpRequest) -> HttpResponse:
    rows = [
        {
            "email": person.email,
            "anchor": person.anchor,
            "note": person.note,
            "last_seen": person.last_seen,
            "total": charts.duration(person.total_seconds),
            "downloads": person.downloads,
            "documents": [
                {
                    "title": read.title,
                    "token": read.token,
                    "strip": charts.strip(
                        read.seconds,
                        read.seen,
                        f"{person.email}, {read.title}",
                        video=read.video,
                        card=read.card,
                    ),
                    "visits": read.visits,
                    "downloads": read.downloads,
                    "total": charts.duration(read.total_seconds),
                    "last_seen": read.last_seen,
                }
                for read in person.documents
            ],
        }
        for person in summary.for_people()
    ]
    context = {"tab": "people", "people": rows, "legend": charts.legend()}
    return render(request, "analytics/people.html", context)
