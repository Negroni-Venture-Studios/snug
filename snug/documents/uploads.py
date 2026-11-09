"""Refuse oversized uploads before Django reads them.

Django's CSRF check reads the whole request body (and writes uploaded files to
disk) before any view runs, so a size check in the view would come too late.
This middleware sits before the CSRF check and refuses a POST whose declared
size is over the limit, without reading it. Refusing changes nothing, so
answering before the CSRF check is safe. Ingest counts the actual bytes too.
"""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

# Room for the form's other fields and the multipart boundaries.
FORM_OVERHEAD = 64 * 1024


def too_large(request: HttpRequest) -> bool:
    if request.method != "POST":
        return False
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return True
    # The larger of the two limits: ingest holds each kind to its own once it knows it.
    limit: int = max(settings.MAX_UPLOAD_BYTES, settings.MAX_VIDEO_BYTES)
    return declared > limit + FORM_OVERHEAD


def upload_size_limit(
    get_response: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    def middleware(request: HttpRequest) -> HttpResponse:
        if too_large(request):
            context = {
                "limit_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024),
                "video_mb": settings.MAX_VIDEO_BYTES // (1024 * 1024),
            }
            return render(request, "documents/too_large.html", context, status=413)
        return get_response(request)

    return middleware
