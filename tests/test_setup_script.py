"""deploy/setup.sh: what it runs on a fresh server and on one already running Snug.

The script runs for real, in a copy of deploy/, with stand-ins for docker, id,
hostname and restore.sh that record what was asked of them. Nothing reaches
Docker or the network.
"""

import os
import pty
import shutil
import stat
import subprocess
import threading
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


def run_setup(
    server: Path,
    *,
    has_database: bool,
    bucket_has_backups: bool,
    answers: str = "",
    succeeds: bool = True,
    terminal: bool = False,
) -> list[str]:
    """Run setup.sh; what it printed is left in `screen`, beside the server folder.

    With `terminal`, its output goes to a pseudo-terminal, as over SSH.
    """
    log = server.parent / "log"
    log.touch()
    env = {
        **os.environ,
        "PATH": f"{server.parent / 'bin'}:{os.environ['PATH']}",
        "LOG": str(log),
        "HAS_DATABASE": "yes" if has_database else "no",
        "BUCKET_HAS_BACKUPS": "yes" if bucket_has_backups else "no",
    }
    # Enter for the domain, then the company's name, then any later answers.
    typed = "\nFernlea Robotics\n" + answers
    if terminal:
        returncode, printed = on_a_terminal(["/bin/sh", str(server / "setup.sh")], typed, env)
    else:
        result = subprocess.run(
            ["/bin/sh", str(server / "setup.sh")],
            input=typed,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        returncode, printed = result.returncode, result.stdout + result.stderr
    (server.parent / "screen").write_text(printed)
    assert (returncode == 0) == succeeds, printed
    return log.read_text().splitlines()


def on_a_terminal(command: list[str], typed: str, env: dict[str, str]) -> tuple[int, str]:
    """Run `command` with its output on a pseudo-terminal and `typed` as its input."""
    controller, terminal = pty.openpty()
    chunks: list[bytes] = []

    def read() -> None:
        while True:
            try:
                chunk = os.read(controller, 4096)
            except OSError:  # Linux: the terminal closed
                return
            if not chunk:  # macOS: the terminal closed
                return
            chunks.append(chunk)

    reader = threading.Thread(target=read)
    reader.start()
    process = subprocess.run(
        command, input=typed.encode(), stdout=terminal, stderr=terminal, env=env, check=False
    )
    os.close(terminal)
    reader.join(timeout=10)
    os.close(controller)
    return process.returncode, b"".join(chunks).decode()


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


# The settings block setup prints for the password manager, as pasted back.
SAVED_SETTINGS = """# Snug's backups: the bucket, and the key that encrypts everything in it.
# Keep a copy of this whole file in your password manager. Without it,
# nobody can read the backups, including you.

[bucket]
type = s3
provider = DigitalOcean
access_key_id = DO00FERNLEA
secret_access_key = fernlea-secret
endpoint = lon1.digitaloceanspaces.com
acl = private
no_check_bucket = true

[snug-backup]
type = crypt
remote = bucket:fernlea-backups/snug
password = fernlea-password
password2 = fernlea-salt
"""


def test_a_new_server_restores_from_pasted_backup_settings(server: Path) -> None:
    (server / "backup" / "rclone.conf").unlink()
    calls = run_setup(
        server,
        has_database=False,
        bucket_has_backups=True,
        answers="yes\n" + SAVED_SETTINGS + "ignored after the last line\n",
    )
    assert "restore.sh " in calls
    assert (server / "backup" / "rclone.conf").read_text() == SAVED_SETTINGS
    assert stat.S_IMODE((server / "backup" / "rclone.conf").stat().st_mode) == 0o600


def test_pasted_settings_with_windows_line_endings_are_cleaned(server: Path) -> None:
    (server / "backup" / "rclone.conf").unlink()
    pasted = SAVED_SETTINGS.replace("\n", "\r\n")
    run_setup(server, has_database=False, bucket_has_backups=True, answers="yes\n" + pasted)
    assert (server / "backup" / "rclone.conf").read_text() == SAVED_SETTINGS


def test_pasted_settings_without_backups_stop_setup(server: Path) -> None:
    (server / "backup" / "rclone.conf").unlink()
    calls = run_setup(
        server,
        has_database=False,
        bucket_has_backups=False,
        answers="yes\n" + SAVED_SETTINGS,
        succeeds=False,
    )
    assert not any(call.startswith("restore.sh") for call in calls)
    assert not any("up --detach" in call for call in calls)
    assert not (server / "backup" / "rclone.conf").exists()


def test_an_incomplete_paste_stops_setup(server: Path) -> None:
    (server / "backup" / "rclone.conf").unlink()
    cut = SAVED_SETTINGS.split("password =")[0]
    calls = run_setup(
        server, has_database=False, bucket_has_backups=True, answers="yes\n" + cut, succeeds=False
    )
    assert not any(call.startswith("restore.sh") for call in calls)
    assert not (server / "backup" / "rclone.conf").exists()


def test_answering_no_asks_for_a_new_bucket(server: Path) -> None:
    (server / "backup" / "rclone.conf").unlink()
    # No, then the region, bucket, access key and secret key, then saved.
    calls = run_setup(
        server,
        has_database=False,
        bucket_has_backups=False,
        answers="no\nams3\nfernlea-backups\nDO00FERNLEA\nfernlea-secret\nsaved\n",
    )
    assert not any(call.startswith("restore.sh") for call in calls)
    settings = (server / "backup" / "rclone.conf").read_text()
    assert "endpoint = ams3.digitaloceanspaces.com" in settings
    assert "remote = bucket:fernlea-backups/snug" in settings


# Clears the screen, then the scrollback, as `clear` does.
CLEAR = "\033[H\033[2J\033[3J"


def test_the_backup_settings_are_cleared_from_the_terminal_once_saved(server: Path) -> None:
    # Found in the audit of 10 November 2026: the settings stayed in the
    # terminal's scrollback after the founder had saved them.
    (server / "backup" / "rclone.conf").unlink()
    answers = "no\nams3\nfernlea-backups\nDO00FERNLEA\nfernlea-secret\nsaved\n"
    run_setup(server, has_database=False, bucket_has_backups=False, answers=answers, terminal=True)
    screen = (server.parent / "screen").read_text()
    shown = screen.index("secret_access_key = fernlea-secret")
    cleared = screen.index(CLEAR)
    assert shown < screen.index("Type saved once it is in your password manager") < cleared
    assert "fernlea-secret" not in screen[cleared:]
    assert "The backup settings have been cleared from the screen." in screen[cleared:]


def test_without_a_terminal_nothing_is_cleared(server: Path) -> None:
    (server / "backup" / "rclone.conf").unlink()
    answers = "no\nams3\nfernlea-backups\nDO00FERNLEA\nfernlea-secret\nsaved\n"
    run_setup(server, has_database=False, bucket_has_backups=False, answers=answers)
    screen = (server.parent / "screen").read_text()
    assert "secret_access_key = fernlea-secret" in screen
    assert "\033[" not in screen
