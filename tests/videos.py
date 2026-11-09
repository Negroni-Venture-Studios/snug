"""Small MP4 files for tests, built box by box, plus a real clip made once with ffmpeg.

tests/fixtures/clip.mp4: 2 seconds of 64x36 H.264 video with AAC audio, made with
    ffmpeg -f lavfi -i testsrc=size=64x36:rate=10 -f lavfi -i sine=frequency=440 \
        -t 2 -c:v libx264 -pix_fmt yuv420p -profile:v baseline -c:a aac -b:a 32k \
        -movflags +faststart -map_metadata -1 clip.mp4
"""

import struct
from pathlib import Path

CLIP = Path(__file__).parent / "fixtures" / "clip.mp4"


def box(kind: str, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind.encode("latin-1") + payload


def full_box(kind: str, payload: bytes, version: int = 0) -> bytes:
    return box(kind, struct.pack(">I", version << 24) + payload)


def _tkhd(width: int, height: int) -> bytes:
    body = struct.pack(">IIIII", 0, 0, 1, 0, 0) + b"\x00" * 8 + b"\x00" * 8 + b"\x00" * 36
    return full_box("tkhd", body + struct.pack(">II", width << 16, height << 16))


def _track(handler: str, sample: str, width: int, height: int) -> bytes:
    hdlr = full_box("hdlr", struct.pack(">I", 0) + handler.encode("latin-1") + b"\x00" * 13)
    entry = box(sample, b"\x00" * 70)
    stsd = full_box("stsd", struct.pack(">I", 1) + entry)
    stbl = box("stbl", stsd)
    minf = box("minf", stbl)
    mdia = box("mdia", hdlr + minf)
    return box("trak", _tkhd(width, height) + mdia)


def make_mp4(
    *,
    major: str = "isom",
    compatible: tuple[str, ...] = ("isom", "avc1", "mp41"),
    seconds: float = 12.5,
    timescale: int = 1000,
    tracks: tuple[tuple[str, str, int, int], ...] = (
        ("vide", "avc1", 1280, 720),
        ("soun", "mp4a", 0, 0),
    ),
    moov_first: bool = True,
    with_mdat: bool = True,
    mvhd_version: int = 0,
) -> bytes:
    ftyp = box(
        "ftyp", major.encode() + struct.pack(">I", 512) + b"".join(c.encode() for c in compatible)
    )
    duration = round(seconds * timescale)
    if mvhd_version == 1:
        mvhd = full_box("mvhd", struct.pack(">QQIQ", 0, 0, timescale, duration) + b"\x00" * 80, 1)
    else:
        mvhd = full_box("mvhd", struct.pack(">IIII", 0, 0, timescale, duration) + b"\x00" * 80)
    moov = box("moov", mvhd + b"".join(_track(*t) for t in tracks))
    mdat = box("mdat", b"\x00" * 64) if with_mdat else b""
    return ftyp + (moov + mdat if moov_first else mdat + moov)
