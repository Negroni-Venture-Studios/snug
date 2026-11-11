"""Which Snug is running, from what the image was built with.

The Image workflow builds a release from its tag, with SNUG_BUILD_VERSION set
to the version ("1.4.2"), and every merge to main as "edge", with the commit
in SNUG_BUILD_COMMIT. Running from a checkout sets neither.
"""

import os
from dataclasses import dataclass

from snug import __version__

REPOSITORY = "https://github.com/Negroni-Venture-Studios/snug"


@dataclass(frozen=True)
class Running:
    name: str  # as shown: "Snug 1.4.2"
    release: str  # the release's version, or "" for an edge build or a checkout
    commit_url: str  # an edge build's commit on GitHub, or ""


def running() -> Running:
    built = os.environ.get("SNUG_BUILD_VERSION", "").strip()
    commit = os.environ.get("SNUG_BUILD_COMMIT", "").strip()
    if built == "edge":
        return Running(f"Snug edge, commit {commit[:7]}", "", f"{REPOSITORY}/commit/{commit}")
    if built:
        return Running(f"Snug {built}", built, "")
    return Running(f"Snug {__version__}, from source", "", "")


def docs_url() -> str:
    """The guides for the running release, or main's for anything else."""
    release = running().release
    return f"{REPOSITORY}/blob/{'v' + release if release else 'main'}/docs/"
