"""snug.office.view: LibreOffice in a view of its container without the shared folders (#205).

LibreOffice runs as the supervisor's own user, so before this it could list
the inbox, write an answer to the outbox while still running, and so be
handed the next file. In its own view it sees only the job's folder and a
read-only system.

Building the view needs user namespaces, which gVisor allows and the
converter's container runs under. Most hosts refuse them outside gVisor
(Ubuntu 24.04, as GitHub's runners are, and Docker's default seccomp
profile), and macOS has none, so the tests of what happens inside the view
run only where one can be built, and the others check that nothing runs
where it cannot.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from snug.office import supervisor, view


def _namespaces_allowed() -> bool:
    unshare = getattr(os, "unshare", None)  # Linux only
    if unshare is None:
        return False
    child = os.fork()
    if child == 0:  # pragma: no cover - the child
        try:
            unshare(view.CLONE_NEWUSER | view.CLONE_NEWNS | view.CLONE_NEWPID)
        except OSError:
            os._exit(1)
        os._exit(0)
    return os.waitstatus_to_exitcode(os.waitpid(child, 0)[1]) == 0


ALLOWED = _namespaces_allowed()
in_a_view = pytest.mark.skipif(not ALLOWED, reason="user namespaces are refused here")
without_a_view = pytest.mark.skipif(ALLOWED, reason="user namespaces are allowed here")


# The program to run in the view: the system's Python, under /usr, rather than
# a virtualenv's link to it, which the view does not hold.
PYTHON = os.path.realpath(sys.executable)


def launch(work: Path, *program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "snug.office.view", str(work), "--", *program],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# ------------------------------------------------------ how the supervisor starts LibreOffice


def test_libreoffice_is_started_through_the_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[list[str], object]] = []

    class Started:
        pid = 1

        def __init__(self, argv: list[str], **options: object) -> None:
            started.append((argv, options.get("preexec_fn")))

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(supervisor, "_is_linux", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Started)
    monkeypatch.setattr(supervisor, "_kill", lambda process: None)
    confine = supervisor.confine_child
    code = supervisor._run(["soffice", "--headless"], tmp_path, {}, 2, confine, 5)
    assert code == 0
    (argv, preexec) = started[0]
    assert argv == [
        sys.executable,
        "-I",
        "-m",
        "snug.office.view",
        str(tmp_path),
        "--",
        "soffice",
        "--headless",
    ]
    assert preexec is None  # the view applies the limits and the filter itself, inside


def test_a_view_that_cannot_be_built_means_no_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Unviewed:
        pid = 1

        def __init__(self, argv: list[str], **options: object) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return view.UNVIEWED

    monkeypatch.setattr(supervisor, "_is_linux", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Unviewed)
    monkeypatch.setattr(supervisor, "_kill", lambda process: None)
    assert supervisor._run(["soffice"], tmp_path, {}, 2, supervisor.confine_child, 5) == (
        "unconfined"
    )


# ---------------------------------------------------------------- where no view can be built


@without_a_view
def test_without_namespaces_the_program_never_runs(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    result = launch(tmp_path, PYTHON, "-c", f"open({str(marker)!r}, 'w')")
    assert result.returncode == view.UNVIEWED, result.stderr
    assert not marker.exists()


# ---------------------------------------------------------------- inside the view


@in_a_view
def test_the_view_holds_the_job_and_nothing_shared(tmp_path: Path) -> None:
    # The stand-in for /exchange: a folder beside the job's, which LibreOffice
    # must neither list nor write to.
    shared = tmp_path / "exchange"
    (shared / "inbox" / "next-token").mkdir(parents=True)
    (shared / "outbox").mkdir()
    work = tmp_path / "job"
    work.mkdir()
    (work / "input.docx").write_text("the file")
    probe = (
        "import os, sys\n"
        f"shared, work = {str(shared)!r}, {str(work)!r}\n"
        "print('job', open(work + '/input.docx').read())\n"
        "print('shared', os.path.exists(shared))\n"
        "try:\n"
        "    open(shared + '/outbox/next-token.pdf', 'wb').write(b'%PDF-')\n"
        "    print('answered early')\n"
        "except OSError:\n"
        "    print('cannot answer')\n"
        "print('processes', sorted(p for p in os.listdir('/proc') if p.isdigit()))\n"
        "status = dict(l.split(':\\t', 1) for l in open('/proc/self/status').read().splitlines())\n"
        "print('capabilities', status['CapEff'].strip(), 'seccomp', status['Seccomp'].strip())\n"
        "print('system writable', os.access('/usr/bin', os.W_OK), os.access('/etc', os.W_OK))\n"
        "open(work + '/out.pdf', 'w').write('made')\n"
    )
    result = launch(work, PYTHON, "-I", "-c", probe)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "job the file",
        "shared False",
        "cannot answer",
        "processes ['1']",
        "capabilities 0000000000000000 seccomp 2",
        "system writable False False",
    ], result.stdout + result.stderr
    assert (work / "out.pdf").read_text() == "made"  # the job's folder is the real one
    assert not (shared / "outbox" / "next-token.pdf").exists()


@in_a_view
def test_the_programs_exit_code_comes_back(tmp_path: Path) -> None:
    assert launch(tmp_path, PYTHON, "-c", "raise SystemExit(81)").returncode == 81


@in_a_view
def test_nothing_it_started_outlives_it(tmp_path: Path) -> None:
    # The program is the first process in its namespace, so when it exits the
    # kernel ends everything else in it: a second line of defence behind the
    # supervisor's own sweep (#203).
    marker = tmp_path / "survived"
    probe = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    time.sleep(2)\n"
        f"    open({str(marker)!r}, 'w')\n"
        "    os._exit(0)\n"
    )
    assert launch(tmp_path, PYTHON, "-I", "-c", probe).returncode == 0
    time.sleep(3)
    assert not marker.exists()
