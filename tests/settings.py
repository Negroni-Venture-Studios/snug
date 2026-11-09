"""Settings for the test suite: Snug's real settings, built from test values.

The values are assigned, not defaulted, so a developer's own SNUG_* variables
never leak into the tests. The secret key is random for each run.
"""

import os
import secrets
import sys
import tempfile
from pathlib import Path

_DATA_DIR = Path(tempfile.mkdtemp(prefix="snug-test-"))
os.environ["SNUG_SECRET_KEY"] = secrets.token_urlsafe(64)
# The end-to-end tests run a real server on localhost over plain HTTP.
_E2E = os.environ.get("SNUG_E2E") == "1"
os.environ["SNUG_DOMAIN"] = "localhost" if _E2E else "snug.example.com"
os.environ["SNUG_DATA_DIR"] = str(_DATA_DIR)
os.environ.pop("SNUG_HTTPS", None)
if _E2E:
    os.environ["SNUG_HTTPS"] = "false"
os.environ.pop("SNUG_DEBUG", None)
# The document worker refuses to run unconfined. Landlock is Linux only, so tests on
# a developer's Mac opt in; on Linux, as in CI, the real confinement is tested.
os.environ.pop("SNUG_UNSAFE_UNCONFINED_RENDERING", None)
if sys.platform != "linux":
    os.environ["SNUG_UNSAFE_UNCONFINED_RENDERING"] = "true"

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

for _folder in ("originals", "renders", "tmp"):
    (_DATA_DIR / _folder).mkdir()

# In production collectstatic fills STATIC_ROOT when the image is built.
STATIC_ROOT = _DATA_DIR / "static"
STATIC_ROOT.mkdir()
