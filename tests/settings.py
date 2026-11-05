"""Settings for the test suite: Snug's real settings, built from test values.

The values are assigned, not defaulted, so a developer's own SNUG_* variables
never leak into the tests. The secret key is random for each run.
"""

import os
import secrets
import tempfile
from pathlib import Path

_DATA_DIR = Path(tempfile.mkdtemp(prefix="snug-test-"))
os.environ["SNUG_SECRET_KEY"] = secrets.token_urlsafe(64)
os.environ["SNUG_DOMAIN"] = "snug.example.com"
os.environ["SNUG_DATA_DIR"] = str(_DATA_DIR)
os.environ.pop("SNUG_HTTPS", None)
os.environ.pop("SNUG_DEBUG", None)

from snug.settings import *  # noqa: E402, F403
from snug.settings import ALLOWED_HOSTS, DATABASES, STORAGES  # noqa: E402

# Django's test client sends requests to "testserver".
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "testserver"]

# A file, not the in-memory default, so WAL mode is real in the tests too.
DATABASES["default"]["TEST"] = {"NAME": str(_DATA_DIR / "test.sqlite3")}

# Tests do not run collectstatic, so skip the manifest.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# In production collectstatic fills STATIC_ROOT when the image is built.
STATIC_ROOT = _DATA_DIR / "static"
STATIC_ROOT.mkdir()
