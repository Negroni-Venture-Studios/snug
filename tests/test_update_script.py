"""deploy/update.sh: bringing a running server's files and images up to date.

The script runs for real, in a copy of deploy/, with stand-ins for curl,
docker, id and install that record what was asked of them. Nothing reaches
GitHub, Docker or the network.
"""

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
FILES = [
    "compose.yaml",
    "Caddyfile",
    "litestream.yml",
    "backup.sh",
    "restore.sh",
    "setup.sh",
    "update.sh",
    "office.sh",
    "snug",
    "snug.env.example",
]

# The release's Compose file, as GitHub serves it: both of Snug's images at its version.
IMAGES = "ghcr.io/negroni-venture-studios"
COMPOSE = (
    f"services:\n  snug:\n    image: ${{SNUG_IMAGE:-{IMAGES}/snug:1.4.2}}\n"
    f"  office:\n    image: ${{SNUG_OFFICE_IMAGE:-{IMAGES}/snug-office:1.4.2}}\n"
)
SNUG_DIGEST = "sha256:" + "5a" * 32
OFFICE_DIGEST = "sha256:" + "0f" * 32

# Writes "new <file>" to the -o path, or the file of that name in $SERVED, unless
# the file is named in $BROKEN, which fails with curl's exit status $BROKEN_EXIT
# (22, an HTTP error, unless set). A release's assets come from $ASSETS, and
# are missing (a 404) if not there. Asked for the HTTP status with -w, it
# prints it: 200, 404, or what $ASSETS/<name>.status holds.
CURL = """#!/bin/sh
echo "curl $*" >> "$LOG"
out=""; url=""; format=""
while [ $# -gt 0 ]; do
	case "$1" in
		-o) out="$2"; shift 2 ;;
		-w) format="$2"; shift 2 ;;
		-*) shift ;;
		*) url="$1"; shift ;;
	esac
done
# The lookup of the newest release prints where GitHub redirected it.
case "$url" in */releases/latest) printf '%s' "$LATEST"; exit 0 ;; esac
name=${url##*/}
[ "$name" = "$BROKEN" ] && exit "${BROKEN_EXIT:-22}"
case "$url" in
	*/releases/download/*)
		if [ -f "$ASSETS/$name.status" ]; then
			echo "an error page" > "$out"; printf '%s' "$(cat "$ASSETS/$name.status")"; exit 0
		fi
		if [ -f "$ASSETS/$name" ]; then
			cp "$ASSETS/$name" "$out"; [ -z "$format" ] || printf 200; exit 0
		fi
		[ -n "$format" ] || exit 22
		echo "Not Found" > "$out"; printf 404; exit 0 ;;
esac
if [ -f "$SERVED/$name" ]; then cp "$SERVED/$name" "$out"; exit 0; fi
# office.sh is sourced, so it must be the real script.
if [ "$name" = office.sh ]; then cp "$OFFICE_SH" "$out"; exit 0; fi
printf 'new %s\\n' "$name" > "$out"
"""


# Where GitHub redirects /releases/latest once there is a release.
NEWEST = "https://github.com/Negroni-Venture-Studios/snug/releases/tag/v1.4.2"


def tool(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def server(tmp_path: Path) -> Path:
    here = tmp_path / "snug"
    here.mkdir()
    shutil.copy(DEPLOY / "update.sh", here / "update.sh")
    for name in FILES:
        if name != "update.sh":
            (here / name).write_text(f"old {name}\n")
    (here / "snug.env").write_text("SNUG_SECRET_KEY='kept'\n")
    (here / "backup").mkdir()
    (here / "backup" / "rclone.conf").write_text("[snug-backup]\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool(bin_dir, "curl", CURL)
    tool(bin_dir, "docker", '#!/bin/sh\necho "docker $*" >> "$LOG"\n')
    tool(bin_dir, "id", "#!/bin/sh\necho 0\n")
    tool(bin_dir, "install", '#!/bin/sh\necho "install $*" >> "$LOG"\n')
    # gVisor's installation: each records what it was asked; runsc is an older release.
    for name in ("apt-get", "gpg", "systemctl"):
        tool(bin_dir, name, f'#!/bin/sh\necho "{name} $*" >> "$LOG"\ncat > /dev/null\n')
    tool(bin_dir, "dpkg", "#!/bin/sh\necho amd64\n")
    tool(
        bin_dir,
        "runsc",
        '#!/bin/sh\necho "runsc $*" >> "$LOG"\n'
        '[ "$1" = --version ] && echo "runsc version release-$RUNSC_RELEASE"\nexit 0\n',
    )
    return here


def served(name: str) -> bytes:
    """A deploy file as the stand-in for curl serves it."""
    if name == "office.sh":
        return (DEPLOY / name).read_bytes()
    if name == "compose.yaml":
        return COMPOSE.encode()
    return f"new {name}\n".encode()


def publish(server: Path, **assets: str | None) -> Path:
    """The release's assets: checksums of what is served, and the two digests.

    A keyword replaces an asset's text (deploy_sha256, snug_image_digest,
    snug_office_image_digest), and None leaves it out.
    """
    folder = server.parent / "assets"
    folder.mkdir(exist_ok=True)
    sums = "".join(f"{hashlib.sha256(served(name)).hexdigest()}  {name}\n" for name in FILES)
    texts: dict[str, str | None] = {
        "deploy.sha256": sums,
        "snug-image.digest": SNUG_DIGEST + "\n",
        "snug-office-image.digest": OFFICE_DIGEST + "\n",
    }
    for key, text in assets.items():
        name = key.replace("_", "-").replace("-sha256", ".sha256").replace("-digest", ".digest")
        assert name in texts, name
        texts[name] = text
    for name, text in texts.items():
        (folder / name).unlink(missing_ok=True)
        if text is not None:
            (folder / name).write_text(text)
    return folder


def run_update(
    server: Path,
    *,
    broken: str = "",
    broken_exit: str = "22",
    latest: str = NEWEST,
    ref: str | None = None,
    args: tuple[str, ...] = (),
    memory_mb: int = 2048,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = server.parent / "log"
    log.touch()
    root = server.parent / "root"
    for folder in ("etc/apt/sources.list.d", "usr/share/keyrings"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    meminfo = server.parent / "meminfo"
    meminfo.write_text(f"MemTotal:       {memory_mb * 1024} kB\n")
    assets = server.parent / "assets"
    if not assets.exists():
        publish(server)
    files = server.parent / "served"
    files.mkdir(exist_ok=True)
    (files / "compose.yaml").write_text(COMPOSE)
    env = {
        **os.environ,
        "PATH": f"{server.parent / 'bin'}:{os.environ['PATH']}",
        "LOG": str(log),
        "BROKEN": broken,
        "BROKEN_EXIT": broken_exit,
        "ASSETS": str(assets),
        "SERVED": str(files),
        "LATEST": latest,
        "OFFICE_SH": str(DEPLOY / "office.sh"),
        "OFFICE_MEMINFO": str(meminfo),
        "OFFICE_ROOT": str(root),
    }
    env.pop("SNUG_REF", None)
    if ref is not None:
        env["SNUG_REF"] = ref
    result = subprocess.run(
        ["/bin/sh", str(server / "update.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, log.read_text().splitlines()


def test_it_replaces_the_files_then_pulls_and_restarts(server: Path) -> None:
    result, calls = run_update(server)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in FILES:
        if name != "compose.yaml":  # pinned, below
            assert (server / name).read_bytes() == served(name), name
    fetched = [call for call in calls if call.startswith("curl") and "/deploy/" in call]
    assert all("/Negroni-Venture-Studios/snug/v1.4.2/deploy/" in call for call in fetched)
    assert f"install -m 755 {server / 'snug'} /usr/local/bin/snug" in calls
    docker = [call for call in calls if call.startswith("docker")]
    assert docker == ["docker compose pull", "docker compose up --detach --wait"]


def test_it_keeps_the_settings_and_the_backup_key(server: Path) -> None:
    run_update(server)
    assert (server / "snug.env").read_text() == "SNUG_SECRET_KEY='kept'\n"
    assert (server / "backup" / "rclone.conf").read_text() == "[snug-backup]\n"


def test_a_failed_download_changes_nothing(server: Path) -> None:
    result, calls = run_update(server, broken="restore.sh")
    assert result.returncode != 0
    assert "Nothing has changed" in result.stdout + result.stderr
    assert (server / "compose.yaml").read_text() == "old compose.yaml\n"
    assert not any(call.startswith(("docker", "install")) for call in calls)
    assert not list(server.parent.glob("snug/.update-*"))


def listed(text: str, start: str) -> list[str]:
    line = next(line for line in text.splitlines() if line.strip().startswith(start))
    return line.split(start, 1)[1].strip().strip('";').removesuffix("; do").split()


def test_it_fetches_the_files_the_release_has_checksums_for() -> None:
    update = listed((DEPLOY / "update.sh").read_text(), 'files="')
    assert update == FILES
    # The workflow takes its list from update.sh, so the two cannot differ.
    workflow = (DEPLOY.parent / ".github/workflows/release-checksums.yml").read_text()
    assert "deploy/update.sh" in workflow


def test_a_new_server_gets_its_files_through_update() -> None:
    # cloud-init runs update.sh rather than downloading the files itself, so a
    # new server's files are checked, and its images pinned, the same way.
    cloud_init = (DEPLOY / "cloud-init.yaml").read_text()
    assert "sh update.sh --files-only" in cloud_init


RELEASES = "https://github.com/Negroni-Venture-Studios/snug/releases"


def test_it_fetches_the_newest_release(server: Path) -> None:
    _, calls = run_update(server, latest=f"{RELEASES}/tag/v1.4.2")
    assert any(call.startswith("curl") and f"{RELEASES}/latest" in call for call in calls)
    fetched = [call for call in calls if "/deploy/" in call]
    assert len(fetched) == len(FILES)
    assert all("/Negroni-Venture-Studios/snug/v1.4.2/deploy/" in call for call in fetched)


@pytest.mark.parametrize("answer", [RELEASES, ""])  # no release yet; the lookup failed
def test_without_a_release_it_changes_nothing(server: Path, answer: str) -> None:
    # Found in the audit of 11 November 2026: falling back to main ran unreleased code.
    result, calls = run_update(server, latest=answer)
    assert result.returncode != 0
    assert "could not find Snug's newest release" in result.stdout + result.stderr
    assert "Nothing has changed" in result.stdout + result.stderr
    assert not any("/deploy/" in call for call in calls)
    assert not any(call.startswith(("docker", "install")) for call in calls)
    assert (server / "compose.yaml").read_text() == "old compose.yaml\n"


def test_snug_ref_chooses_without_asking_github(server: Path) -> None:
    _, calls = run_update(server, latest=f"{RELEASES}/tag/v1.4.2", ref="some-branch")
    assert not any(f"{RELEASES}/latest" in call for call in calls)
    fetched = [call for call in calls if "/deploy/" in call]
    assert all("/snug/some-branch/deploy/" in call for call in fetched)


# ---------------------------------------------------------------- the LibreOffice converter


def env_of(server: Path) -> str:
    return (server / "snug.env").read_text()


def test_with_office_installs_gvisor_and_turns_the_converter_on(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNSC_RELEASE", "20250101")  # an older gVisor, or none
    result, log = run_update(server, args=("--with-office",))
    assert result.returncode == 0, result.stderr
    assert any(line.startswith("gpg --dearmor") for line in log)
    assert "apt-get install --yes -qq runsc" in log
    assert "runsc install" in log and "systemctl restart docker" in log
    assert log.index("runsc install") < log.index("docker compose pull")
    assert "SNUG_OFFICE='true'" in env_of(server)
    assert "COMPOSE_PROFILES='office'" in env_of(server)
    assert "SNUG_SECRET_KEY='kept'" in env_of(server)
    source = (server.parent / "root/etc/apt/sources.list.d/gvisor.list").read_text()
    assert "https://storage.googleapis.com/gvisor/releases 2026" in source
    assert "signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg" in source
    assert "--with-office" not in result.stdout


def test_gvisor_at_the_pinned_release_is_left_alone(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = next(
        line.split("=")[1]
        for line in (DEPLOY / "office.sh").read_text().splitlines()
        if line.startswith("GVISOR_RELEASE=")
    )
    monkeypatch.setenv("RUNSC_RELEASE", pinned)
    result, log = run_update(server, args=("--with-office",))
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("apt-get") for line in log)


def test_with_office_on_a_small_server_warns_about_memory(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNSC_RELEASE", "20250101")
    result, _ = run_update(server, args=("--with-office",), memory_mb=961)
    assert "This server has 961 MB of memory. With LibreOffice, Snug needs 2 GB" in result.stdout


def test_without_office_turns_the_converter_off_and_removes_it(server: Path) -> None:
    (server / "snug.env").write_text(
        "SNUG_SECRET_KEY='kept'\nSNUG_OFFICE='true'\nCOMPOSE_PROFILES='office'\n"
    )
    result, log = run_update(server, args=("--without-office",))
    assert result.returncode == 0, result.stderr
    assert "SNUG_OFFICE='false'" in env_of(server)
    assert "COMPOSE_PROFILES=''" in env_of(server)
    assert "docker compose --profile office rm --stop --force office" in log
    assert "sudo snug update --with-office" not in result.stdout


def test_without_the_converter_update_says_how_to_add_it(server: Path) -> None:
    result, log = run_update(server)
    assert "To show Word, PowerPoint, and Excel files in the browser: sudo snug update" in (
        result.stdout
    )
    assert not any(line.startswith(("apt-get", "runsc")) for line in log)


def test_an_unknown_option_changes_nothing(server: Path) -> None:
    result, log = run_update(server, args=("--with-offices",))
    assert result.returncode == 2
    assert "Usage: sudo snug update [--with-office | --without-office]" in result.stderr
    assert log == []


# ---------------------------------------------------------------- never backwards


def installed(server: Path, version: str) -> None:
    """The release this server runs, as its compose.yaml pins Snug's image."""
    images = "ghcr.io/negroni-venture-studios"
    (server / "compose.yaml").write_text(
        f"services:\n  snug:\n    image: ${{SNUG_IMAGE:-{images}/snug:{version}}}\n"
        f"  office:\n    image: ${{SNUG_OFFICE_IMAGE:-{images}/snug-office:{version}}}\n"
    )


@pytest.mark.parametrize(
    ("running", "newest"),
    [
        ("1.5.0", "v1.4.2"),
        ("1.4.10", "v1.4.9"),  # by number, not by letter
        ("2.0.0", "v1.9.9"),
        ("1.4.2", "v1.4.2-rc.3"),  # a release ranks above its own candidates
    ],
)
def test_it_refuses_to_go_back_to_an_older_release(server: Path, running: str, newest: str) -> None:
    # Issue #169: `releases/latest` is the most recently published release,
    # not the highest version, so a re-published older release would move
    # every updating server back.
    installed(server, running)
    result, calls = run_update(server, latest=f"{RELEASES}/tag/{newest}")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Nothing has changed" in output and f"SNUG_REF={newest}" in output
    assert not [call for call in calls if "/deploy/" in call]
    pinned = (server / "compose.yaml").read_text()
    assert f"ghcr.io/negroni-venture-studios/snug:{running}" in pinned


@pytest.mark.parametrize(
    ("running", "newest"),
    [("1.4.1", "v1.4.2"), ("1.4.2-rc.1", "v1.4.2"), ("1.4.2", "v1.4.2"), ("1.4.9", "v1.4.10")],
)
def test_it_goes_forward_or_stays(server: Path, running: str, newest: str) -> None:
    installed(server, running)
    result, calls = run_update(server, latest=f"{RELEASES}/tag/{newest}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in calls if "/deploy/" in call]


def test_snug_ref_can_go_back_on_purpose(server: Path) -> None:
    installed(server, "1.5.0")
    result, calls = run_update(server, ref="v1.4.2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert any("/v1.4.2/deploy/" in call for call in calls)


# ------------------------------------------------------ checked against the release (#168)


def test_it_pins_the_images_published_with_the_release(server: Path) -> None:
    result, calls = run_update(server)
    assert result.returncode == 0, result.stdout + result.stderr
    compose = (server / "compose.yaml").read_text()
    assert f"{IMAGES}/snug:1.4.2@{SNUG_DIGEST}}}" in compose
    assert f"{IMAGES}/snug-office:1.4.2@{OFFICE_DIGEST}}}" in compose
    downloads = f"{RELEASES}/download/v1.4.2/"
    for asset in ("deploy.sha256", "snug-image.digest", "snug-office-image.digest"):
        assert any(f"{downloads}{asset}" in call for call in calls), asset


def unchanged(server: Path, result: subprocess.CompletedProcess[str], calls: list[str]) -> None:
    assert result.returncode != 0
    assert "Nothing has changed" in result.stdout + result.stderr
    assert (server / "compose.yaml").read_text() == "old compose.yaml\n"
    assert (server / "setup.sh").read_text() == "old setup.sh\n"
    assert not any(call.startswith(("docker", "install")) for call in calls)
    assert not list(server.glob(".update-*"))


def test_a_file_that_does_not_match_its_checksum_changes_nothing(server: Path) -> None:
    # What someone able to change the file between GitHub and this server would cause.
    sums = (publish(server) / "deploy.sha256").read_text()
    wrong = hashlib.sha256(b"something else\n").hexdigest()
    publish(
        server, deploy_sha256=sums.replace(hashlib.sha256(served("setup.sh")).hexdigest(), wrong)
    )
    result, calls = run_update(server)
    unchanged(server, result, calls)
    assert "setup.sh does not match the checksum published with v1.4.2" in result.stderr


def test_a_file_the_checksums_leave_out_changes_nothing(server: Path) -> None:
    sums = (publish(server) / "deploy.sha256").read_text()
    publish(
        server,
        deploy_sha256="".join(
            line + "\n" for line in sums.splitlines() if not line.endswith("  backup.sh")
        ),
    )
    result, calls = run_update(server)
    unchanged(server, result, calls)
    assert "backup.sh does not match the checksum published with v1.4.2" in result.stderr


@pytest.mark.parametrize(
    "missing", ["deploy_sha256", "snug_image_digest", "snug_office_image_digest"]
)
def test_a_release_without_its_checksums_changes_nothing(server: Path, missing: str) -> None:
    publish(server, **{missing: None})
    result, calls = run_update(server)
    unchanged(server, result, calls)
    assert "Try again in a few minutes" in result.stderr


@pytest.mark.parametrize(
    "digest", ["sha256:" + "5a" * 31, "latest", "sha256:" + "5A" * 32, SNUG_DIGEST + "} x"]
)
def test_a_digest_that_is_not_one_changes_nothing(server: Path, digest: str) -> None:
    publish(server, snug_image_digest=digest + "\n")
    result, calls = run_update(server)
    unchanged(server, result, calls)
    assert "is not a digest" in result.stderr


def test_snug_ref_without_checksums_goes_ahead_and_says_so(server: Path) -> None:
    # A branch, or a release candidate, has no release to publish checksums with.
    publish(server, deploy_sha256=None, snug_image_digest=None, snug_office_image_digest=None)
    result, _ = run_update(server, ref="some-branch")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "some-branch has no published checksums" in result.stdout
    assert "@sha256:" not in (server / "compose.yaml").read_text()


def test_snug_ref_with_checksums_still_checks_them(server: Path) -> None:
    sums = (publish(server) / "deploy.sha256").read_text()
    publish(server, deploy_sha256=sums.replace(hashlib.sha256(b"new snug\n").hexdigest(), "0" * 64))
    result, calls = run_update(server, ref="v1.4.2")
    unchanged(server, result, calls)


# ---------------------------------------------------------------- switched in as a set (#171)


def test_an_interrupted_switch_is_finished_before_anything_else(server: Path) -> None:
    # The last update stopped part way through moving its checked files into place.
    staged = server / ".update-new"
    staged.mkdir()
    (staged / ".ready").touch()
    (staged / "backup.sh").write_text("interrupted backup.sh\n")
    (staged / "snug").write_text("interrupted snug\n")
    result, calls = run_update(server, broken="restore.sh")  # and this one fails
    assert "Finishing the update that was interrupted" in result.stdout
    assert (server / "backup.sh").read_text() == "interrupted backup.sh\n"
    assert (server / "snug").read_text() == "interrupted snug\n"
    assert f"install -m 755 {server / 'snug'} /usr/local/bin/snug" in calls
    assert not staged.exists()
    assert "could not download restore.sh" in result.stderr


def test_an_unfinished_download_left_behind_is_discarded(server: Path) -> None:
    staged = server / ".update-new"
    staged.mkdir()
    (staged / "backup.sh").write_text("half downloaded\n")  # never checked: no .ready
    result, _ = run_update(server, broken="restore.sh")
    assert "interrupted" not in result.stdout
    assert (server / "backup.sh").read_text() == "old backup.sh\n"
    assert not staged.exists()


def test_files_only_puts_the_files_in_place_and_starts_nothing(server: Path) -> None:
    # As cloud-init and a hand installation use it, before snug setup.
    for name in FILES:
        (server / name).unlink(missing_ok=True)
    shutil.copy(DEPLOY / "update.sh", server / "update.sh")
    result, calls = run_update(server, args=("--files-only",))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{IMAGES}/snug:1.4.2@{SNUG_DIGEST}}}" in (server / "compose.yaml").read_text()
    assert (server / "snug.env.example").read_bytes() == served("snug.env.example")
    assert f"install -m 755 {server / 'snug'} /usr/local/bin/snug" in calls
    assert not any(call.startswith(("docker", "apt-get", "runsc")) for call in calls)


# -------------------------------------------------- a release's checksums must arrive (F9)


@pytest.mark.parametrize(
    ("how", "status", "exit_code"),
    [
        ("a server error", "503", ""),
        ("a timeout", "", "28"),
        ("a reset", "", "56"),
        ("missing", "", ""),
    ],
)
def test_snug_ref_on_a_release_never_goes_ahead_without_its_checksums(
    server: Path, how: str, status: str, exit_code: str
) -> None:
    # Follow-up audit of 1.2.0, F9: with SNUG_REF set, any failed download of
    # deploy.sha256 was taken for a ref with no checksums, so the release's
    # files went in unchecked and its images unpinned. A release from 1.2.0
    # on always publishes them, so for one, no failure is a reason to go ahead.
    assets = publish(server)
    broken = ""
    if status:
        (assets / "deploy.sha256.status").write_text(status)
    elif exit_code:
        broken = "deploy.sha256"
    else:
        (assets / "deploy.sha256").unlink()  # a 404
    result, calls = run_update(server, ref="v1.4.2", broken=broken, broken_exit=exit_code or "22")
    unchanged(server, result, calls)
    assert "no published checksums" not in result.stdout, how


@pytest.mark.parametrize("ref", ["some-branch", "v1.4.2-rc.1", "v1.1.3"])
def test_snug_ref_without_a_release_that_publishes_checksums_goes_ahead(
    server: Path, ref: str
) -> None:
    # A branch, a release candidate, and a release from before 1.2.0 have no
    # checksums to publish.
    publish(server, deploy_sha256=None, snug_image_digest=None, snug_office_image_digest=None)
    result, _ = run_update(server, ref=ref)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{ref} has no published checksums" in result.stdout


@pytest.mark.parametrize("ref", ["some-branch", "v1.4.2-rc.1", "v1.1.3"])
def test_snug_ref_without_a_release_still_stops_on_a_failed_download(
    server: Path, ref: str
) -> None:
    # Only a 404 says a ref has no checksums. Anything else is a failure.
    (publish(server) / "deploy.sha256.status").write_text("503")
    result, calls = run_update(server, ref=ref)
    unchanged(server, result, calls)
