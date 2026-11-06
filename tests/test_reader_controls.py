"""The reader's page picker leaves room for its arrow.

Every select draws its arrow as a background image at the right, inside
2.4rem of padding. A rule that sets the padding on all four sides takes that
room away, and the arrow sits on top of the page number.
"""

import re

from django.conf import settings


def test_the_page_picker_keeps_room_for_its_arrow() -> None:
    css = (settings.BASE_DIR / "static/snug/css/snug.css").read_text()
    rule = re.search(r"\.reader-controls select \{([^}]*)\}", css)
    assert rule, "no rule for the page picker"
    padding = re.search(r"padding:\s*([^;]+);", rule.group(1))
    assert padding, "the picker's padding is not set"
    sides = padding.group(1).split()
    assert len(sides) == 4, f"set all four sides, so the right keeps its room: {sides}"
    assert float(sides[1].removesuffix("rem")) >= 2.2, sides
