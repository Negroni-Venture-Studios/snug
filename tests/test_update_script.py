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
printf 'new %s\\n' "$name" > "$out"
"""


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
    return here


def run_update(
    server: Path, *, broken: str = "", latest: str = "", ref: str | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = server.parent / "log"
    log.touch()
    env = {
        **os.environ,
        "PATH": f"{server.parent / 'bin'}:{os.environ['PATH']}",
        "LOG": str(log),
        "BROKEN": broken,
        "LATEST": latest,
    }
    env.pop("SNUG_REF", None)
    if ref is not None:
        env["SNUG_REF"] = ref
    result = subprocess.run(
        ["/bin/sh", str(server / "update.sh")],
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
        assert (server / name).read_text() == f"new {name}\n", name
    fetched = [call for call in calls if call.startswith("curl") and "/deploy/" in call]
    assert all("/Negroni-Venture-Studios/snug/main/deploy/" in call for call in fetched)
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


def test_without_a_release_it_fetches_main(server: Path) -> None:
    _, calls = run_update(server, latest=RELEASES)  # GitHub's answer when there is none
    fetched = [call for call in calls if "/deploy/" in call]
    assert all("/snug/main/deploy/" in call for call in fetched)


def test_snug_ref_chooses_without_asking_github(server: Path) -> None:
    _, calls = run_update(server, latest=f"{RELEASES}/tag/v1.4.2", ref="some-branch")
    assert not any(f"{RELEASES}/latest" in call for call in calls)
    fetched = [call for call in calls if "/deploy/" in call]
    assert all("/snug/some-branch/deploy/" in call for call in fetched)
