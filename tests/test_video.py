"""MP4 videos in the confined worker: checked on upload, never decoded.

Decided 9 November 2026: MP4 with H.264 video, which every browser plays,
optionally with AAC audio. The worker reads the file's boxes, in pieces, to
check it and learn its length and size; the investor's browser decodes it.
"""

import io
import struct
from pathlib import Path

import pytest

from snug.rendering import sandbox, video, worker
from tests.test_render_worker import _confined, linux_only
from tests.videos import CLIP, box, make_mp4


def inspect(data: bytes) -> dict[str, object]:
    return video.inspect(io.BytesIO(data), len(data))


# ---------------------------------------------------------------- what is accepted


def test_a_real_clip_is_read() -> None:
    with CLIP.open("rb") as source:
        facts = video.inspect(source, CLIP.stat().st_size)
    assert facts == {"seconds": 2.0, "width": 64, "height": 36, "audio": True}


def test_a_built_video_gives_its_length_and_size() -> None:
    assert inspect(make_mp4()) == {"seconds": 12.5, "width": 1280, "height": 720, "audio": True}


def test_a_video_without_sound_is_fine() -> None:
    facts = inspect(make_mp4(tracks=(("vide", "avc3", 640, 360),)))
    assert facts["audio"] is False


def test_long_headers_and_the_index_at_the_end_are_read() -> None:
    """mvhd version 1 (64-bit times), and moov after mdat, as many cameras write."""
    facts = inspect(make_mp4(mvhd_version=1, moov_first=False, seconds=3600.25))
    assert facts["seconds"] == 3600.25


# ---------------------------------------------------------------- what is refused


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"%PDF-1.7 not a video", "not_mp4"),
        (make_mp4(major="qt  ", compatible=("qt  ",)), "quicktime"),
        (make_mp4(tracks=(("vide", "hvc1", 1920, 1080),)), "codec"),
        (make_mp4(tracks=(("vide", "avc1", 1280, 720), ("soun", "Opus", 0, 0))), "codec"),
        (make_mp4(tracks=(("vide", "encv", 1280, 720),)), "encrypted"),
        (make_mp4(tracks=(("soun", "mp4a", 0, 0),)), "no_video"),
        (make_mp4(seconds=0), "not_mp4"),
        (make_mp4(seconds=5 * 3600), "too_long"),
        (make_mp4(with_mdat=False), "not_mp4"),
        (make_mp4(tracks=(("vide", "avc1", 0, 0),)), "not_mp4"),
    ],
    ids=[
        "not-a-video",
        "quicktime",
        "hevc",
        "opus-audio",
        "encrypted",
        "sound-only",
        "no-length",
        "five-hours",
        "no-media",
        "no-picture-size",
    ],
)
def test_what_is_not_a_plain_h264_mp4_is_refused(data: bytes, code: str) -> None:
    with pytest.raises(video.Refused) as refused:
        inspect(data)
    assert refused.value.code == code


@pytest.mark.parametrize(
    "data",
    [
        # A box claiming more bytes than the file has.
        make_mp4()[:-10],
        # A box smaller than its own header.
        make_mp4()[:28] + struct.pack(">I", 4) + b"moov",
        # A 64-bit size pointing far past the end.
        make_mp4()[:28] + struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 2**62),
        # Two moov boxes: two players could read different ones.
        make_mp4() + box("moov", b""),
    ],
    ids=["truncated", "tiny-box", "huge-64-bit-size", "two-indexes"],
)
def test_broken_box_structures_are_refused(data: bytes) -> None:
    with pytest.raises(video.Refused) as refused:
        inspect(data)
    assert refused.value.code == "not_mp4"


def test_an_index_too_big_to_be_real_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(video, "MAX_INDEX", 64)
    with pytest.raises(video.Refused) as refused:
        inspect(make_mp4())
    assert refused.value.code == "too_large"


def test_the_file_is_read_in_pieces_not_whole() -> None:
    """A 1 GB video must not be read into the worker's memory: only the boxes Snug needs."""

    class Counting(io.BytesIO):
        read_total = 0

        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            Counting.read_total += len(chunk)
            return chunk

    big = make_mp4() + box("free", b"\x00" * 4_000_000)
    video.inspect(Counting(big), len(big))
    assert Counting.read_total < 10_000


# ---------------------------------------------------------------- through the worker


def test_the_worker_inspects_a_video() -> None:
    assert sandbox.inspect_mp4(CLIP) == {"seconds": 2.0, "width": 64, "height": 36, "audio": True}


def test_a_refused_video_says_why_in_plain_words(tmp_path: Path) -> None:
    path = tmp_path / "clip.mov"
    path.write_bytes(make_mp4(major="qt  ", compatible=("qt  ",)))
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.inspect_mp4(path)
    assert str(error.value) == (
        "This is a QuickTime movie. Export it as MP4 with H.264 video, then upload it again."
    )


@pytest.mark.parametrize(
    "output", [b'{"seconds": 0}', b'{"seconds": 2, "width": 64}', b"not json", b"[]"]
)
def test_odd_answers_from_the_worker_are_refused(
    monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    monkeypatch.setattr(sandbox, "run", lambda *args, **kwargs: output)
    with pytest.raises(sandbox.WorkerError) as error:
        sandbox.inspect_mp4(CLIP)
    assert "MP4" in str(error.value)


@linux_only
def test_a_confined_worker_can_read_a_video_through_its_descriptor(tmp_path: Path) -> None:
    """The worker confines itself first; the video arrives as a descriptor opened before."""
    setup = (
        f"import os\nsource = open({str(CLIP)!r}, 'rb')\nsize = os.fstat(source.fileno()).st_size"
    )
    script = "from snug.rendering import video\nprint(video.inspect(source, size)['seconds'])\n"
    result = _confined(script, tmp_path, setup=setup)
    lines = result.stdout.splitlines()
    assert worker.fully_confined(lines[0]), result.stdout + result.stderr
    assert lines[1:] == ["2.0"], result.stdout + result.stderr
