"""A release's files agree on its version."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["project"]["version"])


def test_the_compose_file_runs_this_versions_image() -> None:
    """The deploy files at a release's tag run that release's image, not :edge."""
    compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    found = re.search(
        r"image: \$\{SNUG_IMAGE:-ghcr\.io/negroni-venture-studios/snug:([^}]+)\}", compose
    )
    assert found, "compose.yaml's Snug image line has changed shape"
    assert found.group(1) == project_version()


def test_the_changelog_has_a_section_for_this_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(
        rf"^## \[{re.escape(project_version())}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M
    )
