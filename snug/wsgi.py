"""WSGI entry point, served by gunicorn (see `snug serve`)."""

import os

from django.core.wsgi import get_wsgi_application

from snug.core.hardening import make_undumpable

make_undumpable()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snug.settings")

application = get_wsgi_application()
