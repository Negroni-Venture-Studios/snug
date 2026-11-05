"""The package is installed and reports the version pyproject.toml declares."""

import tomllib
from pathlib import Path

import snug

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject() -> None:
    with (ROOT / "pyproject.toml").open("rb") as f:
        declared = tomllib.load(f)["project"]["version"]
    assert snug.__version__ == declared
