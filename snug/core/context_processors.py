"""Values every template may need."""

from django.http import HttpRequest

from snug.core import brand as brand_colours
from snug.core.models import SiteSettings


def brand(request: HttpRequest) -> dict[str, str]:
    """The address of the founder's colour stylesheet, if they chose a colour.

    Its fingerprint changes with the colour, so browsers fetch a new one at once.
    """
    if not request.path.startswith("/v/"):
        return {}
    accent = SiteSettings.load().accent_colour
    if not accent:
        return {}
    return {"brand_css": f"/brand/brand.css?v={brand_colours.version(accent)}"}
