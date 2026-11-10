"""MP4 videos as documents: uploaded, streamed with a watermark over the player, watched in parts.

Decided 9 November 2026: MP4 with H.264 only; the investor's email, the date
and the link drawn over the player; downloads following the link's setting
(the original file, audited); a separate size limit, 1 GB by default. Time
watched is counted in 10-second parts, each treated as a page, so the
reading charts show which parts were watched.
"""

import time
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.analytics.models import PageView, Visit
from snug.audit.models import AuditEvent
from snug.config import ConfigError, load_config
from snug.documents.ingest import UploadRejectedError, ingest
from snug.documents.models import Document, DocumentVersion
from snug.documents.uploads import FORM_OVERHEAD, too_large
from snug.links import service
from snug.rooms.models import Room, RoomDocument
from tests.pdfs import chunks, make_pdf
from tests.test_config import GOOD
from tests.videos import CLIP, make_mp4

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
CLIP_BYTES = CLIP.read_bytes()


@pytest.fixture(autouse=True)
def own_data_folder(data_folder: Path) -> None:
    """Every test here gets its own data folder (see conftest.py)."""


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


@pytest.fixture
def demo() -> Document:
    document = Document.objects.create(title="Product demo")
    ingest(document, chunks(CLIP_BYTES), filename="demo.mp4", uploaded_by=None)
    return document


@pytest.fixture
def long_demo() -> Document:
    """35 seconds: four parts of 10 seconds. Built box by box, so it streams nothing real."""
    document = Document.objects.create(title="Long demo")
    ingest(document, chunks(make_mp4(seconds=35)), filename="long.mp4", uploaded_by=None)
    return document


def issue(document: Document, **options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "email": INVESTOR,
        "note": "",
        "expires_at": None,
        "require_verification": False,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(document=document, **fields)


def opened(issued: service.Issued) -> Client:
    browser = Client()
    browser.post(f"/v/{issued.token}/open", secure=True)
    return browser


def streamed(response: Any) -> bytes:
    return b"".join(response.streaming_content)


def test_with_no_file_chosen_the_form_names_every_kind(client: Client, demo: Document) -> None:
    """Found in review, 9 November 2026: the message left videos out."""
    wanted = "Choose a PDF, an Excel workbook or an MP4 video to upload."
    response = client.post("/admin/documents/new", {"title": "Demo"}, secure=True)
    assert response.status_code == 400 and wanted in response.content.decode()
    response = client.post(f"/admin/documents/{demo.token}/versions", {}, secure=True)
    assert wanted in response.content.decode()


# ---------------------------------------------------------------- uploading


def test_a_video_becomes_a_video_document(demo: Document) -> None:
    version = demo.latest_version()
    assert version is not None
    assert version.kind == DocumentVersion.Kind.MP4
    assert (version.seconds, version.page_count, version.page_sizes) == (2.0, 1, [[64, 36]])
    assert version.stored_name.endswith(".mp4")
    assert version.describe == "MP4 video, 0:02"


def test_a_video_is_counted_in_ten_second_parts(long_demo: Document) -> None:
    version = long_demo.latest_version()
    assert version is not None
    assert version.page_count == 4
    assert version.describe == "MP4 video, 0:35"


def test_a_quicktime_movie_is_refused_plainly() -> None:
    document = Document.objects.create(title="Demo")
    movie = make_mp4(major="qt  ", compatible=("qt  ",))
    with pytest.raises(UploadRejectedError) as refused:
        ingest(document, chunks(movie), filename="demo.mov", uploaded_by=None)
    assert str(refused.value) == (
        "This is a QuickTime movie. Export it as MP4 with H.264 video, then upload it again."
    )


def test_videos_have_their_own_size_limit() -> None:
    """A video may be larger than a document; each kind is held to its own limit."""
    with override_settings(MAX_UPLOAD_BYTES=1024, MAX_VIDEO_BYTES=64 * 1024):
        ingest(
            Document.objects.create(title="Demo"),
            chunks(CLIP_BYTES),
            filename="d.mp4",
            uploaded_by=None,
        )
        with pytest.raises(UploadRejectedError) as refused:
            ingest(
                Document.objects.create(title="Deck"),
                chunks(make_pdf(*[(960, 540)] * 3)),
                filename="deck.pdf",
                uploaded_by=None,
            )
        assert "the most this instance accepts for a document" in str(refused.value)
    with override_settings(MAX_UPLOAD_BYTES=64 * 1024, MAX_VIDEO_BYTES=1024):
        with pytest.raises(UploadRejectedError) as refused:
            ingest(
                Document.objects.create(title="Demo"),
                chunks(CLIP_BYTES),
                filename="d.mp4",
                uploaded_by=None,
            )
        assert "the most this instance accepts for a video" in str(refused.value)


def from_an_admin(request: HttpRequest) -> HttpRequest:
    request.user = Admin(email="ruth@fernlea.example", is_active=True)
    return request


@pytest.mark.parametrize("path", ["/admin/documents/new", "/admin/documents/AbCdEf123_-x/versions"])
def test_the_request_limit_allows_a_video_sized_upload(path: str) -> None:
    request = from_an_admin(RequestFactory().post(path, CONTENT_LENGTH=str(50 * 1024 * 1024)))
    with override_settings(MAX_UPLOAD_BYTES=10 * 1024 * 1024, MAX_VIDEO_BYTES=100 * 1024 * 1024):
        assert not too_large(request)
    with override_settings(MAX_UPLOAD_BYTES=10 * 1024 * 1024, MAX_VIDEO_BYTES=20 * 1024 * 1024):
        assert too_large(request)


@pytest.mark.parametrize(
    "path",
    ["/v/sometoken/code/send", "/admin/login", "/admin/settings/logo", "/admin/documents/new/x"],
)
def test_only_the_upload_forms_get_the_video_limit(path: str) -> None:
    """Found in review: every POST, an investor's included, could send a video-sized body."""
    request = from_an_admin(RequestFactory().post(path, CONTENT_LENGTH=str(50 * 1024 * 1024)))
    with override_settings(MAX_UPLOAD_BYTES=10 * 1024 * 1024, MAX_VIDEO_BYTES=100 * 1024 * 1024):
        assert too_large(request)


@pytest.mark.parametrize("path", ["/admin/documents/new", "/v/sometoken/code/send"])
def test_anyone_else_gets_only_a_forms_fields(path: str) -> None:
    """Found by an external audit: anyone could send a video-sized body to the upload forms."""
    request = RequestFactory().post(path, CONTENT_LENGTH=str(FORM_OVERHEAD + 1))
    request.user = AnonymousUser()
    assert too_large(request)
    request = RequestFactory().post(path, CONTENT_LENGTH=str(FORM_OVERHEAD))
    request.user = AnonymousUser()
    assert not too_large(request)


def test_the_video_limit_is_a_setting() -> None:
    assert load_config(GOOD).max_video_mb == 1024
    assert load_config({**GOOD, "SNUG_MAX_VIDEO_MB": "2048"}).max_video_mb == 2048
    with pytest.raises(ConfigError) as error:
        load_config({**GOOD, "SNUG_MAX_VIDEO_MB": "lots"})
    assert "SNUG_MAX_VIDEO_MB must be a whole number of megabytes" in str(error.value)


def test_a_document_keeps_its_kind(demo: Document) -> None:
    with pytest.raises(UploadRejectedError) as refused:
        ingest(demo, chunks(make_pdf((960, 540))), filename="deck.pdf", uploaded_by=None)
    assert str(refused.value) == (
        "Product demo is a video. Upload a video as its new version, "
        "or upload the PDF as a new document."
    )


def test_the_upload_forms_offer_videos(client: Client, demo: Document) -> None:
    for url in ("/admin/documents/new", f"/admin/documents/{demo.token}"):
        page = client.get(url, secure=True).content.decode()
        assert ",video/mp4,.mp4" in page, url


# ---------------------------------------------------------------- the investor's player


def test_a_video_link_shows_the_player_with_the_watermark(demo: Document) -> None:
    issued = issue(demo)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    assert f'src="/v/{issued.token}/video"' in page
    assert 'controlslist="nofullscreen nodownload"' in page
    assert "disablepictureinpicture" in page
    assert f"Shared with {INVESTOR}" in page and f"link {issued.link.label}" in page
    assert 'video-fullscreen" type="button"' in page  # full screen keeps the watermark
    assert "/pages/" not in page
    assert AuditEvent.objects.filter(action="viewer.document_viewed").count() == 1
    (view,) = PageView.objects.all()
    assert view.page == 1


def test_the_about_panel_describes_a_video(demo: Document) -> None:
    """On the page before opening, where the investor reads what will be recorded."""
    issued = issue(demo)
    page = Client().get(f"/v/{issued.token}", secure=True).content.decode()
    assert "Product demo, a video, 0:02" in page
    assert "<dt>Access</dt><dd>Watch in the browser, or download</dd>" in page
    assert "how long you watch each part" in page


def test_the_video_streams_in_ranges(demo: Document) -> None:
    issued = issue(demo)
    browser = opened(issued)
    url = f"/v/{issued.token}/video"
    start = browser.get(url, secure=True, HTTP_RANGE="bytes=0-")  # what a player asks first
    assert start.status_code == 206
    assert start["Content-Type"] == "video/mp4"
    assert start["Accept-Ranges"] == "bytes"
    assert streamed(start) == CLIP_BYTES
    part = browser.get(url, secure=True, HTTP_RANGE="bytes=100-199")
    assert part.status_code == 206
    assert part["Content-Range"] == f"bytes 100-199/{len(CLIP_BYTES)}"
    assert part["Content-Length"] == "100"
    assert streamed(part) == CLIP_BYTES[100:200]
    rest = browser.get(url, secure=True, HTTP_RANGE="bytes=13000-")
    assert streamed(rest) == CLIP_BYTES[13000:]
    tail = browser.get(url, secure=True, HTTP_RANGE="bytes=-50")
    assert streamed(tail) == CLIP_BYTES[-50:]
    past = browser.get(url, secure=True, HTTP_RANGE="bytes=99999999-")
    assert past.status_code == 416
    assert past["Content-Range"] == f"bytes */{len(CLIP_BYTES)}"


def test_the_stream_answers_only_ranges_and_in_pieces(
    demo: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found in review: a plain request got the whole file. A player always asks for ranges,
    and each answer is capped, so the stream is no easier to save than a download."""
    from snug.core import ranges

    issued = issue(demo, allow_download=False)
    browser = opened(issued)
    url = f"/v/{issued.token}/video"
    assert browser.get(url, secure=True).status_code == 416  # no Range header
    monkeypatch.setattr(ranges, "MAX_PIECE", 1000)
    piece = browser.get(url, secure=True, HTTP_RANGE="bytes=0-")
    assert piece.status_code == 206
    assert piece["Content-Range"] == f"bytes 0-999/{len(CLIP_BYTES)}"
    assert streamed(piece) == CLIP_BYTES[:1000]


def test_the_link_form_says_a_streamed_video_can_be_saved(client: Client, demo: Document) -> None:
    page = client.get(f"/admin/links/for/{demo.token}", secure=True).content.decode()
    assert "Someone determined can save a video they are watching, even with downloads off." in page


def test_a_revoked_link_stops_the_video_at_once(demo: Document) -> None:
    issued = issue(demo)
    browser = opened(issued)
    service.revoke(issued.link, by=None)
    response = browser.get(f"/v/{issued.token}/video", secure=True, HTTP_RANGE="bytes=0-99")
    assert response.status_code not in (200, 206)
    assert response.get("Content-Type", "") != "video/mp4"


def test_a_video_has_no_page_images(demo: Document) -> None:
    issued = issue(demo)
    assert opened(issued).get(f"/v/{issued.token}/pages/1-800.webp", secure=True).status_code == 404


def test_reaching_a_new_part_moves_the_clock(long_demo: Document) -> None:
    issued = issue(long_demo)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    visit = Visit.objects.get()
    token = browser.cookies["csrftoken"].value if "csrftoken" in browser.cookies else ""
    form = {"visit": visit.token, "page": "3", "kind": "part", "csrfmiddlewaretoken": token}
    assert browser.post(f"/v/{issued.token}/beat", form, secure=True).status_code == 204
    visit.refresh_from_db()
    assert visit.current_page == 3
    assert sorted(PageView.objects.values_list("page", flat=True)) == [1, 3]
    form["page"] = "5"  # past the last part
    assert browser.post(f"/v/{issued.token}/beat", form, secure=True).status_code == 400
    assert 'data-part-seconds="10"' in page


def test_the_download_is_the_original_video(demo: Document) -> None:
    issued = issue(demo)
    response = opened(issued).get(f"/v/{issued.token}/download", secure=True)
    assert response.status_code == 200
    assert response["Content-Type"] == "video/mp4"
    assert 'filename="Product demo.mp4"' in response["Content-Disposition"]
    assert streamed(response) == CLIP_BYTES
    version = demo.latest_version()
    assert version is not None
    event = AuditEvent.objects.get(action="viewer.downloaded")
    assert event.details["sha256"] == version.sha256


def test_with_downloads_off_there_is_no_download(demo: Document) -> None:
    issued = issue(demo, allow_download=False)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "/download" not in page
    assert browser.get(f"/v/{issued.token}/download", secure=True).status_code == 404


def test_a_room_lists_and_plays_a_video(demo: Document) -> None:
    room = Room.objects.create(name="Series A")
    RoomDocument.objects.create(room=room, document=demo)
    issued = service.create(
        room=room,
        email=INVESTOR,
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
    )
    browser = opened(issued)
    assert "MP4 video, 0:02" in browser.get(f"/v/{issued.token}", secure=True).content.decode()
    player = browser.get(f"/v/{issued.token}/d/{demo.token}", secure=True).content.decode()
    assert f'src="/v/{issued.token}/d/{demo.token}/video"' in player
    stream = browser.get(
        f"/v/{issued.token}/d/{demo.token}/video", secure=True, HTTP_RANGE="bytes=0-"
    )
    assert stream.status_code == 206


def test_the_security_policy_allows_media_from_snug_alone(demo: Document) -> None:
    issued = issue(demo)
    response = opened(issued).get(f"/v/{issued.token}", secure=True)
    assert "media-src 'self'" in response["Content-Security-Policy"]


# ---------------------------------------------------------------- the admin's pages


def test_the_admin_pages_describe_a_video(client: Client, demo: Document) -> None:
    assert "MP4 video, 0:02" in client.get("/admin/documents/", secure=True).content.decode()
    page = client.get(f"/admin/documents/{demo.token}", secure=True).content.decode()
    assert "MP4 video, 0:02" in page
    assert f'src="/admin/documents/{demo.token}/v1/video"' in page
    assert "First page" not in page
    preview = client.get(
        f"/admin/documents/{demo.token}/v1/video", secure=True, HTTP_RANGE="bytes=0-9"
    )
    assert preview.status_code == 206 and streamed(preview) == CLIP_BYTES[:10]
    assert (
        client.get(f"/admin/documents/{demo.token}/v1/page-1.webp", secure=True).status_code == 404
    )


def test_the_admin_preview_needs_a_signed_in_admin(demo: Document) -> None:
    response = Client().get(f"/admin/documents/{demo.token}/v1/video", secure=True)
    assert response.status_code == 302


def test_watching_shows_in_parts_on_the_admin_pages(client: Client, long_demo: Document) -> None:
    issued = issue(long_demo)
    opened(issued).get(f"/v/{issued.token}", secure=True)
    page = client.get(f"/admin/documents/{long_demo.token}", secure=True).content.decode()
    assert "How it was watched" in page
    assert "0:10" in page  # the axis counts time, not pages
    people = client.get("/admin/people/", secure=True).content.decode()
    assert "0:00: not seen" not in people and "0:00:" in people


def test_the_link_form_says_what_a_video_download_is(client: Client, demo: Document) -> None:
    page = client.get(f"/admin/links/for/{demo.token}", secure=True).content.decode()
    assert (
        "The original video file, without a watermark; the audit log records who downloaded it."
        in page
    )


def test_the_admin_uploads_a_video(client: Client) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile("demo.mp4", CLIP_BYTES, content_type="video/mp4")
    response = client.post("/admin/documents/new", {"title": "Demo", "pdf": upload}, secure=True)
    page = client.get(response["Location"], secure=True).content.decode()
    assert "Uploaded, an MP4 video, 0:02 long. Create a link to share it." in page
    event = AuditEvent.objects.get(action="document.version_uploaded")
    assert (event.details["kind"], event.details["seconds"]) == ("mp4", 2.0)
