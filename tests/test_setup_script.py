"""deploy/setup.sh: what it runs on a fresh server and on one already running Snug.

The script runs for real, in a copy of deploy/, with stand-ins for docker, id,
hostname and restore.sh that record what was asked of them. Nothing reaches
Docker or the network.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"

DOCKER = """#!/bin/sh
echo "docker $*" >> "$LOG"
case "$*" in
	*"--entrypoint test snug -e /var/lib/snug/snug.sqlite3"*) [ "$HAS_DATABASE" = yes ] ;;
	*"--entrypoint rclone backup lsf snug-backup:database"*)
		[ "$BUCKET_HAS_BACKUPS" = yes ] && echo 0001.ltx ;;
	*) exit 0 ;;
esac
"""


def tool(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def server(tmp_path: Path) -> Path:
    here = tmp_path / "snug"
    shutil.copytree(DEPLOY, here)
    (here / "restore.sh").write_text('echo "restore.sh $*" >> "$LOG"\n')
    (here / "backup").mkdir()
    (here / "backup" / "rclone.conf").write_text("[snug-backup]\ntype = crypt\n")
    (here / "snug.env").write_text("SNUG_SECRET_KEY='kept'\nSNUG_DOMAIN='room.fernlea.example'\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool(bin_dir, "docker", DOCKER)
    tool(bin_dir, "id", "#!/bin/sh\necho 0\n")
    tool(bin_dir, "hostname", "#!/bin/sh\necho 203.0.113.5\n")
    tool(bin_dir, "getent", "#!/bin/sh\necho 203.0.113.5 STREAM room.fernlea.example\n")
    tool(bin_dir, "chown", "#!/bin/sh\nexit 0\n")
    return here


def run_setup(server: Path, *, has_database: bool, bucket_has_backups: bool) -> list[str]:
    log = server.parent / "log"
    log.touch()
    env = {
        **os.environ,
        "PATH": f"{server.parent / 'bin'}:{os.environ['PATH']}",
        "LOG": str(log),
        "HAS_DATABASE": "yes" if has_database else "no",
        "BUCKET_HAS_BACKUPS": "yes" if bucket_has_backups else "no",
    }
    # Enter for the domain, then the company's name.
    result = subprocess.run(
        ["/bin/sh", str(server / "setup.sh")],
        input="\nFernlea Robotics\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return log.read_text().splitlines()


def test_a_second_run_keeps_the_data_and_restarts_snug(server: Path) -> None:
    calls = run_setup(server, has_database=True, bucket_has_backups=True)
    assert not any(call.startswith("restore.sh") for call in calls)
    assert not any("createadmin" in call for call in calls)
    assert calls[-1] == "docker compose up --detach --wait"
    assert "SNUG_COMPANY_NAME='Fernlea Robotics'" in (server / "snug.env").read_text()


def test_a_new_server_with_backups_restores_them(server: Path) -> None:
    calls = run_setup(server, has_database=False, bucket_has_backups=True)
    assert "restore.sh " in calls


def test_a_new_server_without_backups_starts_afresh(server: Path) -> None:
    calls = run_setup(server, has_database=False, bucket_has_backups=False)
    assert not any(call.startswith("restore.sh") for call in calls)
    assert "docker compose up --detach --wait" in calls


def test_snug_creates_the_data_volume_before_the_backup_container_mounts_it(
    server: Path,
) -> None:
    calls = run_setup(server, has_database=False, bucket_has_backups=True)
    mounts = [call for call in calls if call.startswith("docker compose run")]
    assert "--entrypoint true snug" in mounts[0]
