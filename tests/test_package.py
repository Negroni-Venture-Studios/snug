"""The package is installed and reports the version pyproject.toml declares."""

import tomllib
from pathlib import Path

import snug

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject() -> None:
    with (ROOT / "pyproject.toml").open("rb") as f:
        declared = tomllib.load(f)["project"]["version"]
    assert snug.__version__ == declared


def test_importing_snug_reads_no_package_metadata() -> None:
    # The document worker imports the package for every page it renders or
    # stamps. Reading the installed metadata took half a second of each start
    # on a one-CPU server (measured 12 November 2026).
    import subprocess
    import sys

    code = "import sys, snug.rendering.worker; print('importlib.metadata' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=True
    )
    assert result.stdout.strip() == "False"
