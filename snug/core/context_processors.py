"""Values every template may need."""

from django.http import HttpRequest

from snug.core import brand as brand_colours
from snug.core import logo
from snug.core.models import SiteSettings, company_name


def brand(request: HttpRequest) -> dict[str, str]:
    """The founder's colour stylesheet, logo and tab icon, for viewer pages.

    Each address changes with what it serves, so browsers fetch a new one at once.
    """
    if not request.path.startswith("/v/"):
        return {}
    row = SiteSettings.load()
    found = {}
    if row.accent_colour:
        found["brand_css"] = f"/brand/brand.css?v={brand_colours.version(row.accent_colour)}"
    found["brand_icon"], found["brand_icon_type"] = logo.favicon(
        row.logo_name, row.icon_name, company_name(), row.accent_colour
    )
    if row.logo_name:
        found["brand_logo"] = f"/brand/{row.logo_name}"
        if not row.show_name_with_logo:
            found["brand_logo_only"] = "1"
    return found
