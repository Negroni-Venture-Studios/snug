"""deploy/update.sh: bringing a running server's files and images up to date.

The script runs for real, in a copy of deploy/, with stand-ins for curl,
docker, id and install that record what was asked of them. Nothing reaches
GitHub, Docker or the network.
"""

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
]

# Writes "new <file>" to the -o path, unless the file is named in $BROKEN.
CURL = """#!/bin/sh
echo "curl $*" >> "$LOG"
# The lookup of the newest release prints where GitHub redirected it.
case " $* " in *" -w "*) printf '%s' "$LATEST"; exit 0 ;; esac
out=""; url=""
while [ $# -gt 0 ]; do
	case "$1" in
		-o) out="$2"; shift 2 ;;
		-*) shift ;;
		*) url="$1"; shift ;;
	esac
done
name=${url##*/}
[ "$name" = "$BROKEN" ] && exit 22
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


def run_update(
    server: Path,
    *,
    broken: str = "",
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
    env = {
        **os.environ,
        "PATH": f"{server.parent / 'bin'}:{os.environ['PATH']}",
        "LOG": str(log),
        "BROKEN": broken,
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
        # office.sh is sourced, so the stand-in gives the real one.
        new = (DEPLOY / name).read_text() if name == "office.sh" else f"new {name}\n"
        assert (server / name).read_text() == new, name
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


def test_it_fetches_the_same_files_as_a_new_server() -> None:
    cloud_init = listed((DEPLOY / "cloud-init.yaml").read_text(), "for file in")
    update = listed((DEPLOY / "update.sh").read_text(), 'files="')
    assert update == cloud_init == FILES


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
