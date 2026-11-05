from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
@login_required
def overview(request: HttpRequest) -> HttpResponse:
    return render(request, "core/overview.html", {"tab": "overview"})


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """Django's own page uses inline styles, which our CSP forbids. This one does not."""
    return render(request, "core/csrf_failure.html", status=403)
