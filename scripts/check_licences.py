"""Fail if any installed package has a licence incompatible with MIT.

Snug is MIT licensed. GPL and AGPL dependencies would stop us distributing it
that way, which is why Snug uses none of PyMuPDF, Ghostscript and Poppler.
This checks every installed package, direct or transitive, using pip-licenses.

Run: uv run python scripts/check_licences.py
"""

import json
import subprocess
import sys

# Permissive licences compatible with distributing Snug as MIT, matched as
# case-insensitive substrings of the licence pip-licenses reports. MPL-2.0 is
# file-level copyleft and allows larger works under any licence. LGPL permits
# use from non-copyleft code when, as here, the library can be replaced.
COMPATIBLE = (
    "mit",
    "bsd",
    "apache",
    "isc",
    "psf",
    "python software foundation",
    "mpl",
    "mozilla public license",
    "lgpl",
    "gnu lesser",
    "unlicense",
    "cc0",
    "public domain",
    "hpnd",  # Pillow's MIT-CMU is a historical permission notice
    "zlib",
)

# Copyleft that would bind Snug. Checked first, so "GPL" never passes because
# a string also happens to mention a permissive licence.
INCOMPATIBLE = ("agpl", "affero", "gpl-2", "gpl-3", "gplv2", "gplv3", "general public license v")

SELF = {"snug"}


def classify(licence: str) -> bool:
    text = licence.strip().lower()
    if "lgpl" not in text and "lesser" not in text and any(bad in text for bad in INCOMPATIBLE):
        return False
    return any(ok in text for ok in COMPATIBLE)


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--from=mixed"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"pip-licenses failed:\n{result.stderr}", file=sys.stderr)
        return 1

    problems = []
    packages = json.loads(result.stdout)
    for pkg in packages:
        name, licence = pkg["Name"], pkg["License"]
        if name.lower() in SELF:
            continue
        if not classify(licence):
            problems.append(f"  {name} {pkg['Version']}: {licence}")

    if problems:
        print("Packages with licences not known to be compatible with MIT:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print("Check each by hand; add a pattern only once it is confirmed.", file=sys.stderr)
        return 1
    print(f"Licences: {len(packages)} packages checked, all compatible with MIT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
