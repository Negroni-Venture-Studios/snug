"""Snug's own system checks, shown by `snug check` and when `snug serve` starts."""

from typing import Any

from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security)
def unconfined_rendering(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    if not settings.CONFIG.unsafe_unconfined_rendering:
        return []
    return [
        checks.Warning(
            "SNUG_UNSAFE_UNCONFINED_RENDERING is on: PDFs are read unconfined wherever "
            "the worker cannot confine itself, so a hostile PDF could reach the database "
            "and every document.",
            hint="Use it only for development on a machine without Landlock, never on a server.",
            id="snug.W001",
        )
    ]
