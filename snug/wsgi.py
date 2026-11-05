"""WSGI entry point, served by gunicorn (see `snug serve`)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snug.settings")

application = get_wsgi_application()
