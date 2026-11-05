"""The licence gate accepts permissive licences and refuses copyleft ones."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_licences import classify


@pytest.mark.parametrize(
    "licence",
    [
        "MIT License",
        "BSD-3-Clause",
        "Apache Software License",
        "Apache-2.0 OR BSD-3-Clause",
        "MIT-CMU",
        "HPND",
        "Mozilla Public License 2.0 (MPL 2.0)",
        "GNU Lesser General Public License v3 (LGPLv3)",
        "ISC License (ISCL)",
        "Python Software Foundation License",
    ],
)
def test_permissive_licences_pass(licence: str) -> None:
    assert classify(licence)


@pytest.mark.parametrize(
    "licence",
    [
        "GNU Affero General Public License v3",
        "AGPL-3.0-only",
        "GNU General Public License v3 (GPLv3)",
        "GPL-2.0-or-later",
        "GPL-3.0 OR MIT",  # dual licences are judged by hand, not passed automatically
        "UNKNOWN",
        "",
    ],
)
def test_copyleft_and_unknown_licences_fail(licence: str) -> None:
    assert not classify(licence)
