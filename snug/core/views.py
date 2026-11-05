from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from snug.analytics import charts, summary
from snug.analytics.models import Visit
from snug.documents.models import Document
from snug.links.models import Link


@never_cache
@require_safe
@login_required
def overview(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    documents = Document.objects.count()
    context: dict[str, object] = {"tab": "overview", "documents": documents}
    if documents:
        live = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        context["stats"] = {
            "links": Link.objects.filter(live, revoked_at__isnull=True).count(),
            "people": Visit.objects.values("email").distinct().count(),
            "week": Visit.objects.filter(started_at__gte=now - timedelta(days=7)).count(),
        }
        context["recent"] = [
            {"visit": visit, "time": charts.duration(visit.seconds)}
            for visit in summary.recent_visits()
        ]
    return render(request, "core/overview.html", context)


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """Django's own page uses inline styles, which our CSP forbids. This one does not."""
    return render(request, "core/csrf_failure.html", status=403)
