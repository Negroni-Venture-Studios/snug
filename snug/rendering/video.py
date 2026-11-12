"""MP4 videos, checked inside the confined worker without being decoded.

An MP4 is a sequence of boxes, each a 32-bit size and a four-letter type.
Snug walks the top-level boxes by reading their headers alone and seeking
past the rest, so a large video is never read into memory: only the index
(moov), which is small, is read whole. From it Snug learns the length and
picture size, and checks every track's codec: H.264 video (avc1 or avc3),
which every browser plays, and optionally AAC audio (mp4a). QuickTime movies,
other codecs and encrypted tracks are refused. The investor's browser decodes
the video; Snug never does.

Standard library only, like snug.rendering.ooxml.
"""

import struct
from typing import BinaryIO

MAX_TOP_LEVEL_BOXES = 100_000
# The index of a long, high-frame-rate video is a few megabytes at most.
MAX_INDEX = 64 * 1024 * 1024
MAX_SECONDS = 4 * 60 * 60
MAX_SIDE = 16_384
VIDEO_CODECS = {"avc1", "avc3"}
AUDIO_CODECS = {"mp4a"}
ENCRYPTED = {"encv", "enca"}


class Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _top_level(source: BinaryIO, size: int) -> dict[str, list[tuple[int, int]]]:
    """Every top-level box's payload position and length, by type. Nothing else is read."""
    found: dict[str, list[tuple[int, int]]] = {}
    position, count = 0, 0
    while position < size:
        count += 1
        if count > MAX_TOP_LEVEL_BOXES:
            raise Refused("not_mp4")
        source.seek(position)
        header = source.read(8)
        if len(header) < 8:
            raise Refused("not_mp4")
        length, kind = struct.unpack(">I4s", header)
        start = position + 8
        if length == 1:  # a 64-bit size follows
            extended = source.read(8)
            if len(extended) < 8:
                raise Refused("not_mp4")
            (length,) = struct.unpack(">Q", extended)
            start += 8
        elif length == 0:  # to the end of the file
            length = size - position
        if length < start - position or position + length > size:
            raise Refused("not_mp4")
        found.setdefault(kind.decode("latin-1"), []).append((start, position + length - start))
        position += length
    return found


def _children(data: bytes, start: int = 0, end: int | None = None) -> list[tuple[str, bytes]]:
    """The boxes inside a box already in memory."""
    end = len(data) if end is None else end
    boxes, position = [], start
    while position + 8 <= end:
        length, kind = struct.unpack_from(">I4s", data, position)
        header = 8
        if length == 1:
            if position + 16 > end:
                raise Refused("not_mp4")
            (length,) = struct.unpack_from(">Q", data, position + 8)
            header = 16
        elif length == 0:
            length = end - position
        if length < header or position + length > end:
            raise Refused("not_mp4")
        boxes.append((kind.decode("latin-1"), data[position + header : position + length]))
        position += length
    if position != end:
        raise Refused("not_mp4")
    return boxes


def _first(boxes: list[tuple[str, bytes]], kind: str) -> bytes:
    for name, payload in boxes:
        if name == kind:
            return payload
    raise Refused("not_mp4")


def _length(mvhd: bytes) -> float:
    try:
        if mvhd[0] == 1:
            timescale, duration = struct.unpack_from(">IQ", mvhd, 20)
        else:
            timescale, duration = struct.unpack_from(">II", mvhd, 12)
    except (IndexError, struct.error) as error:
        raise Refused("not_mp4") from error
    if timescale == 0 or duration == 0:
        raise Refused("not_mp4")
    seconds = duration / timescale
    if seconds > MAX_SECONDS:
        raise Refused("too_long")
    return float(round(seconds, 3))


def _track(trak: bytes) -> tuple[str, list[str], int, int]:
    """A track's handler (vide, soun...), its sample formats, and its picture size."""
    boxes = _children(trak)
    tkhd = _first(boxes, "tkhd")
    try:
        offset = 88 if tkhd[0] == 1 else 76
        width, height = (v >> 16 for v in struct.unpack_from(">II", tkhd, offset))
        mdia = _children(_first(boxes, "mdia"))
        handler = _first(mdia, "hdlr")[8:12].decode("latin-1")
        stbl = _children(_first(_children(_first(mdia, "minf")), "stbl"))
        stsd = _first(stbl, "stsd")
        formats = [kind for kind, _payload in _children(stsd, 8)]
    except (IndexError, struct.error) as error:
        raise Refused("not_mp4") from error
    return handler, formats, width, height


def inspect(source: BinaryIO, size: int) -> dict[str, object]:
    """The length in seconds, the picture size, and whether there is sound."""
    found = _top_level(source, size)
    ftyp = found.get("ftyp", [])
    if len(ftyp) != 1:
        raise Refused("not_mp4")
    source.seek(ftyp[0][0])
    brands = source.read(min(ftyp[0][1], 1024))
    if brands[:4] == b"qt  ":
        raise Refused("quicktime")
    if len(found.get("moov", [])) != 1 or not (found.get("mdat") or found.get("moof")):
        raise Refused("not_mp4")
    start, length = found["moov"][0]
    if length > MAX_INDEX:
        raise Refused("too_large")
    source.seek(start)
    moov = _children(source.read(length))

    seconds = _length(_first(moov, "mvhd"))
    width = height = 0
    audio = False
    for kind, trak in moov:
        if kind != "trak":
            continue
        handler, formats, track_width, track_height = _track(trak)
        if any(f in ENCRYPTED for f in formats):
            raise Refused("encrypted")
        if handler == "vide":
            if not formats or any(f not in VIDEO_CODECS for f in formats):
                raise Refused("codec")
            if not (0 < track_width <= MAX_SIDE and 0 < track_height <= MAX_SIDE):
                raise Refused("not_mp4")
            width, height = width or track_width, height or track_height
        elif handler == "soun":
            if any(f not in AUDIO_CODECS for f in formats):
                raise Refused("codec")
            audio = True
    if not width:
        raise Refused("no_video")
    return {"seconds": seconds, "width": width, "height": height, "audio": audio}
