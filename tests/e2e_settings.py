"""Settings for the end-to-end tests: a real server on localhost, over plain HTTP.

    uv run playwright install chromium
    SNUG_E2E=1 uv run pytest tests/e2e --ds=tests.e2e_settings

A plain `uv run pytest` skips them.
"""

import os

os.environ["SNUG_E2E"] = "1"
# Playwright's synchronous API runs an event loop in the test's thread, which
# Django would otherwise take for async code and refuse database calls from.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

from tests.settings import *  # noqa: F403
