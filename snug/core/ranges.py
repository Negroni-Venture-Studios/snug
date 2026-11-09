"""Serving a file in byte ranges, as browsers ask for video.

A video element fetches its file in pieces (HTTP Range requests) to start
quickly and to seek; Safari will not play an MP4 without them. Only a single
range is honoured: a request for several, or one Snug cannot read, gets the
whole file, as RFC 9110 allows. A range that starts past the end gets 416.

The caller has already decided the request may see the file: this module
only reads it, in chunks, so a large video never sits in memory.
"""

import re
from collections.abc import Iterator
from pathlib import Path

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase

CHUNK = 256 * 1024
# The most one answer carries. A player asks again for the rest, as it does when
# seeking, so this costs playback nothing; it only makes a stream no easier to
# save whole than the file would be.
MAX_PIECE = 8 * 1024 * 1024
_SINGLE = re.compile(r"^bytes=(\d*)-(\d*)$")


class Unsatisfiable(Exception):
    """The range starts past the end of the file."""


def parse(header: str, size: int) -> tuple[int, int] | None:
    """The first and last byte asked for, inclusive; None for the whole file."""
    found = _SINGLE.match(header.strip())
    if found is None:
        return None
    first, last = found.groups()
    if not first and not last:
        return None
    if not first:  # the last N bytes
        count = int(last)
        if count == 0:
            raise Unsatisfiable
        return max(0, size - count), size - 1
    start = int(first)
    if start >= size:
        raise Unsatisfiable
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, end


def _chunks(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as source:
        source.seek(start)
        remaining = length
        while remaining > 0:
            chunk = source.read(min(CHUNK, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


def _refused(size: int) -> HttpResponse:
    refused = HttpResponse(status=416)
    refused["Content-Range"] = f"bytes */{size}"
    return refused


def serve(
    request: HttpRequest, path: Path, content_type: str, *, ranges_only: bool = False
) -> HttpResponseBase:
    """The file, or the one range of it the request asks for, at most MAX_PIECE at a time.

    With ranges_only, a request without a usable range is refused: a video
    element always asks for one, and a stream should not hand over the whole
    file in one answer.
    """
    size = path.stat().st_size
    try:
        wanted = parse(request.headers.get("Range", ""), size)
    except Unsatisfiable:
        return _refused(size)
    if wanted is None and ranges_only:
        return _refused(size)
    if wanted is not None:
        wanted = (wanted[0], min(wanted[1], wanted[0] + MAX_PIECE - 1))
    start, end = wanted if wanted is not None else (0, size - 1)
    length = end - start + 1
    response = StreamingHttpResponse(_chunks(path, start, length), content_type=content_type)
    if wanted is not None:
        response.status_code = 206
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    return response
