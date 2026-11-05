"""The stylesheet, scripts, fonts and logo are found and collected."""

from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import override_settings

REQUIRED = [
    "snug/css/snug.css",
    "snug/js/theme-init.js",
    "snug/js/theme.js",
    "snug/fonts/Geist-Variable.woff2",
    "snug/fonts/GeistMono-Variable.woff2",
    "snug/img/favicon.svg",
    "snug/img/snug-glyph.svg",
    "snug/icons/sign-out.svg",
]


@pytest.mark.parametrize("path", REQUIRED)
def test_static_files_are_found(path: str) -> None:
    assert finders.find(path), f"{path} is not found by the static file finders"


def test_collectstatic_builds_a_manifest_with_hashed_names(tmp_path: Path) -> None:
    storages = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
    with override_settings(STATIC_ROOT=tmp_path, STORAGES=storages):
        call_command("collectstatic", interactive=False, verbosity=0)
    assert (tmp_path / "staticfiles.json").is_file()
    css = next(tmp_path.glob("snug/css/snug.*.css"))
    # Font URLs in the stylesheet are rewritten to their hashed names.
    assert "Geist-Variable." in css.read_text(encoding="utf-8")
    assert "Geist-Variable.woff2'" not in css.read_text(encoding="utf-8")
