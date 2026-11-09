"""Byte ranges, as a video element asks for them (RFC 9110, section 14)."""

import pytest

from snug.core.ranges import Unsatisfiable, parse


@pytest.mark.parametrize(
    ("header", "wanted"),
    [
        ("bytes=0-99", (0, 99)),
        ("bytes=100-", (100, 999)),
        ("bytes=-50", (950, 999)),
        ("bytes=-5000", (0, 999)),  # more than the file: the whole file, as a range
        ("bytes=900-5000", (900, 999)),  # past the end: up to the end
        ("", None),
        ("bytes=0-99,200-299", None),  # several ranges: the whole file
        ("items=0-5", None),
        ("bytes=-", None),
        ("bytes=50-10", None),  # backwards: ignored
    ],
)
def test_ranges_are_read_as_browsers_send_them(header: str, wanted: tuple[int, int] | None) -> None:
    assert parse(header, 1000) == wanted


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=5000-6000", "bytes=-0"])
def test_a_range_past_the_end_cannot_be_met(header: str) -> None:
    with pytest.raises(Unsatisfiable):
        parse(header, 1000)
