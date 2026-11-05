from django.urls import URLPattern

# The health check is answered by snug.core.middleware.health_check, before
# routing. Application URLs arrive with the admin interface and the viewer.
urlpatterns: list[URLPattern] = []
