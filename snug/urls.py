from django.urls import include, path
from django.views.generic import RedirectView

from snug.core.views import brand_css, brand_logo

# The health check is answered by snug.core.middleware.health_check, before
# routing. Everything a viewer sees is under v/, behind snug.viewer.access.
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="core:overview", permanent=False)),
    path("admin/", include("snug.core.urls")),
    path("admin/", include("snug.accounts.urls")),
    path("admin/documents/", include("snug.documents.urls")),
    path("admin/links/", include("snug.links.urls")),
    path("admin/rooms/", include("snug.rooms.urls")),
    path("admin/people/", include("snug.analytics.urls")),
    path("admin/audit/", include("snug.audit.urls")),
    path("v/", include("snug.viewer.urls")),
    # The founder's accent colour and logo for viewer pages. Public: neither is a secret.
    path("brand/brand.css", brand_css, name="brand_css"),
    path("brand/<str:name>", brand_logo, name="brand_logo"),
]
