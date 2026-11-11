"""The document worker runs apart from Snug: limited, confined and given nothing secret."""

import errno
import io
import json
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from snug.rendering import sandbox, worker
from tests.pdfs import make_pdf

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Landlock and prctl are Linux")


@pytest.fixture
def deck(tmp_path: Path) -> Path:
    path = tmp_path / "deck.pdf"
    path.write_bytes(make_pdf((960, 540), (595, 842)))
    return path


def size_of(webp: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(webp)) as image:
        assert image.format == "WEBP"
        return image.size


# ---------------------------------------------------------------- across the process boundary


def test_inspect(deck: Path) -> None:
    assert sandbox.inspect(deck) == {"pages": 2, "sizes": [[960, 540], [595, 842]]}


def test_render_returns_webp_through_the_pipe(deck: Path) -> None:
    assert size_of(sandbox.render(deck, 1, 1600)) == (1600, 900)


def test_very_tall_pages_are_capped(tmp_path: Path) -> None:
    pdf = tmp_path / "tall.pdf"
    pdf.write_bytes(make_pdf((10, 14000)))
    width, height = size_of(sandbox.render(pdf, 1, 1600))
    assert width * height <= worker.MAX_PIXELS
    assert max(width, height) <= worker.MAX_SIDE


def test_missing_page(deck: Path) -> None:
    with pytest.raises(sandbox.WorkerError, match="does not exist"):
        sandbox.render(deck, 9, 1600)


def test_bad_arguments(deck: Path) -> None:
    with pytest.raises(sandbox.WorkerError):
        sandbox.run(deck, "explode", timeout=30)


def test_the_worker_gets_no_secrets() -> None:
    env = sandbox.worker_env()
    assert not [k for k in env if k.startswith(("SNUG_", "DJANGO_"))]
    assert set(env) == {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE"}


def test_the_worker_runs_isolated_and_is_given_no_path() -> None:
    argv = [arg for arg in sandbox.command("inspect", "3") if arg != worker.ALLOW_UNCONFINED]
    assert argv[:4] == [sys.executable, "-I", "-m", "snug.rendering.worker"]
    assert argv[4:] == ["inspect", "3"]


def test_the_worker_limits_itself() -> None:
    probe = (
        "import resource, json; from snug.rendering import worker; worker.apply_limits(); "
        "print(json.dumps({n: resource.getrlimit(getattr(resource, n))[0] "
        "for n in ('RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NOFILE', 'RLIMIT_CORE', 'RLIMIT_AS', "
        "'RLIMIT_NPROC')}))"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    limits = json.loads(out)
    assert limits["RLIMIT_CPU"] == worker.CPU_SECONDS
    assert limits["RLIMIT_FSIZE"] == worker.OUTPUT_BYTES
    assert limits["RLIMIT_NOFILE"] == worker.OPEN_FILES
    assert limits["RLIMIT_CORE"] == 0
    assert limits["RLIMIT_NPROC"] == 0
    if sys.platform == "linux":
        assert limits["RLIMIT_AS"] == worker.MEMORY_BYTES


def test_timeouts_kill_the_worker(deck: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sandbox, "command", lambda *a: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    with pytest.raises(sandbox.WorkerError, match="took too long"):
        sandbox.run(deck, "inspect", timeout=0.5)


@pytest.mark.parametrize(
    ("returncode", "code"),
    [(-9, "memory"), (-24, "unreadable"), (-11, "unreadable")],
    ids=["killed", "cpu", "segfault"],
)
def test_crashes_become_errors(
    deck: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, code: str
) -> None:
    script = f"import os, signal; os.kill(os.getpid(), {-returncode})"
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", script])
    with pytest.raises(sandbox.WorkerError) as caught:
        sandbox.run(deck, "inspect", timeout=30)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "output",
    [b"print('hello')", b'import sys; sys.stdout.write(\'{"pages": 3, "sizes": []}\')'],
    ids=["not-json", "inconsistent"],
)
def test_untrustworthy_output_is_refused(
    deck: Path, monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", output.decode()])
    with pytest.raises(sandbox.WorkerError) as caught:
        sandbox.inspect(deck)
    assert caught.value.code == "unreadable"


def test_a_render_that_is_not_webp_is_refused(deck: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = "import sys; sys.stdout.buffer.write(b'<html>not an image</html>')"
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", script])
    with pytest.raises(sandbox.WorkerError):
        sandbox.render(deck, 1, 1600)


def test_errors_without_json_are_unreadable(deck: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sandbox, "command", lambda *a: [sys.executable, "-c", "raise SystemExit(3)"]
    )
    with pytest.raises(sandbox.WorkerError) as caught:
        sandbox.run(deck, "inspect", timeout=30)
    assert caught.value.code == "unreadable"


# ---------------------------------------------------------------- confinement (Linux)


def _confined(script: str, tmp_path: Path, setup: str = "") -> subprocess.CompletedProcess[str]:
    """Run `setup`, then confine as the worker does, then run `script`.

    Anything the script needs must be imported in `setup`: once confined, not
    even Python's own library files can be opened.
    """
    secret = tmp_path / "snug.sqlite3"
    secret.write_text("TOTP keys live here", encoding="utf-8")
    code = (
        f"{setup}\nfrom snug.rendering import worker; worker.preload(); print(worker.confine())\n"
        f"SECRET = {str(secret)!r}\n" + script
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30, check=False
    )


@linux_only
def test_a_confined_worker_cannot_open_files(tmp_path: Path) -> None:
    result = _confined("open(SECRET).read()", tmp_path)
    assert result.stdout.startswith("landlock v"), result.stdout + result.stderr
    assert "PermissionError" in result.stderr


@linux_only
def test_a_confined_worker_cannot_open_any_socket(tmp_path: Path) -> None:
    # Landlock's network rules cover TCP only; a UDP packet still left the
    # confined worker until 6 November 2026 (security audit). A seccomp filter
    # now refuses every socket, of any kind, before it exists.
    script = (
        "for family, kind in ((socket.AF_INET, socket.SOCK_DGRAM),\n"
        "                     (socket.AF_INET, socket.SOCK_STREAM),\n"
        "                     (socket.AF_INET6, socket.SOCK_DGRAM),\n"
        "                     (socket.AF_UNIX, socket.SOCK_STREAM)):\n"
        "    try:\n"
        "        socket.socket(family, kind); print('OPENED', family, kind)\n"
        "    except PermissionError:\n"
        "        print('refused')\n"
    )
    result = _confined(script, tmp_path, setup="import socket")
    assert result.stdout.splitlines()[0].endswith(", no sockets"), result.stdout + result.stderr
    assert result.stdout.splitlines()[1:] == ["refused"] * 4, result.stdout + result.stderr


@linux_only
def test_a_confined_worker_reports_full_confinement(tmp_path: Path) -> None:
    result = _confined("", tmp_path)
    how = result.stdout.strip()
    assert worker.fully_confined(how), how
    abi = int(how.split()[1].rstrip(",").lstrip("v"))
    assert abi >= worker.MIN_LANDLOCK_ABI


@linux_only
def test_a_confined_worker_can_still_render(deck: Path) -> None:
    # The full path, confined: fonts and every lazy import must already be loaded.
    assert size_of(sandbox.render(deck, 2, 800))[0] in (800, 801)


@linux_only
def test_undumpable_processes_hide_their_environment() -> None:
    code = (
        "import os, subprocess, sys\n"
        "from snug.core.hardening import make_undumpable\n"
        "assert make_undumpable()\n"
        'child = \'import os; open(f"/proc/{os.getppid()}/environ", "rb").read()\'\n'
        "r = subprocess.run([sys.executable, '-c', child], capture_output=True, text=True)\n"
        "print('hidden' if 'PermissionError' in r.stderr else 'READABLE')\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "SNUG_SECRET_KEY": "x" * 60},
    )
    assert result.stdout.strip() == "hidden", result.stdout + result.stderr


def test_make_undumpable_is_a_no_op_elsewhere() -> None:
    from snug.core.hardening import make_undumpable

    if sys.platform != "linux":
        assert make_undumpable() is False


def test_preload_loads_every_text_codec_pypdf_uses() -> None:
    # Once confined, the worker cannot open Python's own files, so a codec
    # pypdf loads lazily (UTF-16 for metadata, found 6 November 2026) fails.
    code = (
        "import sys; from snug.rendering import worker; worker.preload(); "
        "print(' '.join(m for m in sys.modules if m.startswith('encodings.')))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=True
    )
    loaded = set(result.stdout.split())
    for module in (
        "utf_16",
        "utf_16_be",
        "utf_16_le",
        "utf_8",
        "latin_1",
        "cp1252",
        "charmap",
        "mac_roman",
        "iso8859_15",
        "ascii",
    ):
        assert f"encodings.{module}" in loaded, module


def test_confine_reports_when_unavailable() -> None:
    if sys.platform != "linux":
        assert worker.confine() == "unconfined: not Linux"


@pytest.mark.parametrize(
    ("how", "full"),
    [
        ("landlock v4, no sockets", True),
        ("landlock v6, no sockets", True),
        ("landlock v3, no sockets", False),  # no network rules: below the minimum
        ("landlock v4, socket filter refused", False),
        ("landlock v4, socket filter unavailable", False),
        ("unconfined: Landlock unavailable", False),
        ("unconfined: ruleset refused", False),
        ("unconfined: restrict_self refused", False),
        ("unconfined: not Linux", False),
    ],
)
def test_what_counts_as_confined(how: str, full: bool) -> None:
    assert worker.fully_confined(how) is full


# ---------------------------------------------------------------- the seccomp filter, on any OS

# What a seccomp program can return, from linux/seccomp.h.
SECCOMP_ALLOW, SECCOMP_EPERM, SECCOMP_KILL = 0x7FFF0000, 0x00050001, 0x80000000
# The calls the filter names, from each architecture's syscall table.
SYSCALLS = {
    "x86_64": (
        0xC000003E,
        {
            "read": 0,
            "write": 1,
            "mmap": 9,
            "getpid": 39,
            "socket": 41,
            "kill": 62,
            "ptrace": 101,
            "rt_sigqueueinfo": 129,
            "tkill": 200,
            "tgkill": 234,
            "rt_tgsigqueueinfo": 297,
            "process_vm_readv": 310,
            "process_vm_writev": 311,
            "pidfd_send_signal": 424,
            "io_uring_setup": 425,
            "pidfd_open": 434,
            "pidfd_getfd": 438,
            "setpriority": 141,
            "sched_setparam": 142,
            "sched_setscheduler": 144,
            "sched_setaffinity": 203,
            "ioprio_set": 251,
            "migrate_pages": 256,
            "move_pages": 279,
            "prlimit64": 302,
            "kcmp": 312,
            "process_madvise": 440,
            "sched_setattr": 314,
            "process_mrelease": 448,
        },
    ),
    "aarch64": (
        0xC00000B7,
        {
            "read": 63,
            "write": 64,
            "mmap": 222,
            "getpid": 172,
            "socket": 198,
            "kill": 129,
            "ptrace": 117,
            "rt_sigqueueinfo": 138,
            "tkill": 130,
            "tgkill": 131,
            "rt_tgsigqueueinfo": 240,
            "process_vm_readv": 270,
            "process_vm_writev": 271,
            "pidfd_send_signal": 424,
            "io_uring_setup": 425,
            "pidfd_open": 434,
            "pidfd_getfd": 438,
            "ioprio_set": 30,
            "sched_setparam": 118,
            "sched_setscheduler": 119,
            "sched_setaffinity": 122,
            "setpriority": 140,
            "migrate_pages": 238,
            "move_pages": 239,
            "prlimit64": 261,
            "kcmp": 272,
            "process_madvise": 440,
            "sched_setattr": 274,
            "process_mrelease": 448,
        },
    ),
}
WORKER_PID = 4242
SIGNALLING = ("kill", "tkill", "tgkill", "rt_sigqueueinfo", "rt_tgsigqueueinfo")


def _seccomp(program: bytes, arch: int, nr: int, *args: int) -> int:
    """What the kernel would do with one call: a small interpreter for the BPF the filter uses."""
    words = [(arg & 0xFFFF_FFFF_FFFF_FFFF) for arg in (*args, 0, 0, 0, 0, 0, 0)[:6]]
    data = struct.pack("<iIQ6Q", nr, arch, 0, *words)  # struct seccomp_data
    ops = [struct.unpack("HBBI", program[i : i + 8]) for i in range(0, len(program), 8)]
    accumulator = position = 0
    while True:
        code, jump_true, jump_false, k = ops[position]
        position += 1
        if code == 0x20:  # BPF_LD | BPF_W | BPF_ABS
            accumulator = struct.unpack_from("<I", data, k)[0]
        elif code == 0x15:  # BPF_JMP | BPF_JEQ | BPF_K
            position += jump_true if accumulator == k else jump_false
        elif code == 0x35:  # BPF_JMP | BPF_JGE | BPF_K
            position += jump_true if accumulator >= k else jump_false
        elif code == 0x06:  # BPF_RET | BPF_K
            return int(k)
        else:
            pytest.fail(f"unexpected BPF instruction {code:#x}")


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_filter_refuses_sockets_and_reaching_into_other_processes(machine: str) -> None:
    # Security audit, 10 November 2026: below Landlock ABI 6 nothing stopped a
    # compromised worker tracing or reading the memory of Snug's own processes.
    arch, numbers = SYSCALLS[machine]
    program = worker._seccomp_filter(machine, WORKER_PID)
    assert program is not None
    for name in (
        "socket",
        "io_uring_setup",
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
        "pidfd_open",
        "pidfd_getfd",
        "pidfd_send_signal",
        # Re-prioritising, re-scheduling or moving the memory of a process,
        # even the worker's own: nothing it runs does (review, 11 November 2026).
        "setpriority",
        "ioprio_set",
        "sched_setaffinity",
        "sched_setscheduler",
        "sched_setparam",
        "kcmp",
        "move_pages",
        "migrate_pages",
        "process_madvise",
        "sched_setattr",
        "process_mrelease",
    ):
        for target in (0, WORKER_PID, WORKER_PID + 1, 1):
            assert _seccomp(program, arch, numbers[name], target) == SECCOMP_EPERM, name


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_filter_lets_the_worker_change_only_its_own_limits(machine: str) -> None:
    # glibc's getrlimit and setrlimit call prlimit64 with 0, meaning the caller.
    arch, numbers = SYSCALLS[machine]
    program = worker._seccomp_filter(machine, WORKER_PID)
    assert program is not None
    for target in (0, WORKER_PID):
        assert _seccomp(program, arch, numbers["prlimit64"], target, 7) == SECCOMP_ALLOW
    for target in (WORKER_PID + 1, 1, -1, -WORKER_PID):
        assert _seccomp(program, arch, numbers["prlimit64"], target, 7) == SECCOMP_EPERM, target


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_filter_lets_the_worker_signal_only_itself(machine: str) -> None:
    arch, numbers = SYSCALLS[machine]
    program = worker._seccomp_filter(machine, WORKER_PID)
    assert program is not None
    for name in SIGNALLING:
        # abort() is tgkill(own pid, own thread, SIGABRT): the worker may signal itself.
        assert _seccomp(program, arch, numbers[name], WORKER_PID, WORKER_PID, 6) == SECCOMP_ALLOW
        # Another process, its own group (0), every process (-1) or another group.
        for target in (WORKER_PID + 1, 1, 0, -1, -WORKER_PID, -(WORKER_PID + 1)):
            assert _seccomp(program, arch, numbers[name], target, 9) == SECCOMP_EPERM, (
                name,
                target,
            )


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_filter_allows_everything_else(machine: str) -> None:
    arch, numbers = SYSCALLS[machine]
    program = worker._seccomp_filter(machine, WORKER_PID)
    assert program is not None
    for name in ("read", "write", "mmap", "getpid"):
        assert _seccomp(program, arch, numbers[name], WORKER_PID + 1) == SECCOMP_ALLOW, name


def test_the_filter_kills_calls_from_any_other_architecture() -> None:
    program = worker._seccomp_filter("x86_64", WORKER_PID)
    assert program is not None
    assert _seccomp(program, 0x40000003, 3) == SECCOMP_KILL  # 32-bit x86, read
    assert _seccomp(program, 0xC000003E, 0x40000000 | 0) == SECCOMP_EPERM  # x32, read
    arm = worker._seccomp_filter("aarch64", WORKER_PID)
    assert arm is not None
    assert _seccomp(arm, 0x40000028, 3) == SECCOMP_KILL  # 32-bit Arm, read


def test_there_is_no_filter_for_an_unknown_architecture() -> None:
    assert worker._seccomp_filter("riscv64", WORKER_PID) is None


@linux_only
def test_a_confined_worker_can_signal_only_itself(tmp_path: Path) -> None:
    script = (
        "def attempt(pid):\n"
        "    try:\n"
        "        os.kill(pid, 0)\n"
        "        return 'sent'\n"
        "    except PermissionError:\n"
        "        return 'refused'\n"
        "    except ProcessLookupError:\n"
        "        return 'no such process'\n"
        "print(attempt(os.getpid()), attempt(os.getppid()), attempt(0), attempt(-1))\n"
        "signal.signal(signal.SIGUSR1, lambda *_: print('caught'))\n"
        "signal.raise_signal(signal.SIGUSR1)\n"
    )
    result = _confined(script, tmp_path, setup="import os, signal")
    lines = result.stdout.splitlines()
    assert lines[0].endswith(", no sockets"), result.stdout + result.stderr
    assert lines[1:] == ["sent refused refused refused", "caught"], result.stdout + result.stderr


@linux_only
def test_a_confined_worker_cannot_change_another_process(tmp_path: Path) -> None:
    # Each would succeed unconfined: reading the parent's limit, and setting
    # the worker's own priority and processors to what they already are.
    script = (
        "def attempt(call, *args):\n"
        "    try:\n"
        "        call(*args)\n"
        "        return 'done'\n"
        "    except PermissionError:\n"
        "        return 'refused'\n"
        "print(resource.getrlimit(resource.RLIMIT_NOFILE)[0] > 0)\n"
        "print(attempt(resource.prlimit, os.getppid(), resource.RLIMIT_NOFILE),\n"
        "      attempt(os.setpriority, os.PRIO_PROCESS, 0, os.getpriority(os.PRIO_PROCESS, 0)),\n"
        "      attempt(os.sched_setaffinity, 0, os.sched_getaffinity(0)))\n"
    )
    result = _confined(script, tmp_path, setup="import os, resource")
    lines = result.stdout.splitlines()
    assert lines[0].endswith(", no sockets"), result.stdout + result.stderr
    assert lines[1:] == ["True", "refused refused refused"], result.stdout + result.stderr


@linux_only
def test_a_confined_worker_can_still_abort(tmp_path: Path) -> None:
    # glibc's abort() signals the worker itself; refused, it would die another way.
    setup = "import os, resource, sys; resource.setrlimit(resource.RLIMIT_CORE, (0, 0))"
    result = _confined("sys.stdout.flush(); os.abort()", tmp_path, setup=setup)
    assert result.stdout.startswith("landlock v"), result.stdout + result.stderr
    assert result.returncode == -signal.SIGABRT


@linux_only
def test_a_confined_worker_cannot_trace_or_read_processes(tmp_path: Path) -> None:
    # Each call would succeed unconfined: tracing by its parent, and reading
    # nothing from its own memory. Confined, both are refused before they start.
    script = (
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "print(libc.process_vm_readv(os.getpid(), None, 0, None, 0, 0), ctypes.get_errno())\n"
        "print(libc.ptrace(0, 0, None, None), ctypes.get_errno())  # PTRACE_TRACEME\n"
    )
    result = _confined(script, tmp_path, setup="import ctypes, os")
    lines = result.stdout.splitlines()
    assert lines[0].endswith(", no sockets"), result.stdout + result.stderr
    assert lines[1:] == [f"-1 {errno.EPERM}"] * 2, result.stdout + result.stderr


# ---------------------------------------------------------------- failing closed


@pytest.fixture
def no_limits(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """main() with confinement on, but without limiting the test process itself."""
    parsed: list[str] = []

    def inspect(data: bytes) -> dict[str, int]:
        parsed.append("parsed")
        return {"pages": 1}

    monkeypatch.setattr(worker, "apply_limits", lambda: True)
    monkeypatch.setattr(worker, "inspect", inspect)
    return parsed


def test_the_worker_says_when_its_memory_limit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import resource

    applied: list[int] = []

    def setrlimit(kind: int, limits: tuple[int, int]) -> None:
        if kind == resource.RLIMIT_AS:
            raise ValueError("not here")
        applied.append(kind)

    monkeypatch.setattr(resource, "setrlimit", setrlimit)
    monkeypatch.setattr(worker, "_is_linux", lambda: True)
    assert worker.apply_limits() is False
    assert resource.RLIMIT_NPROC in applied  # the others are still set
    # macOS does not enforce the limit, and the worker cannot confine itself there.
    monkeypatch.setattr(worker, "_is_linux", lambda: False)
    assert worker.apply_limits() is True
    monkeypatch.setattr(resource, "setrlimit", lambda kind, limits: None)
    monkeypatch.setattr(worker, "_is_linux", lambda: True)
    assert worker.apply_limits() is True


@pytest.mark.parametrize("mode", ["inspect", "inspect-mp4"])
def test_the_worker_refuses_to_read_without_its_memory_limit(
    deck: Path,
    mode: str,
    no_limits: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Security audit, 10 November 2026: the worker carried on without a memory
    # limit if setting it failed, even fully confined on Linux.
    monkeypatch.setattr(worker, "apply_limits", lambda: False)
    monkeypatch.setattr(worker, "confine", lambda: "landlock v6, no sockets")
    assert worker.main([mode, _fd(deck)]) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "unconfined"}
    assert no_limits == []


def test_without_a_memory_limit_only_the_operator_opt_in_reads(
    deck: Path, no_limits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "apply_limits", lambda: False)
    monkeypatch.setattr(worker, "confine", lambda: "unconfined: not Linux")
    assert worker.main([worker.ALLOW_UNCONFINED, "inspect", _fd(deck)]) == 0
    assert no_limits == ["parsed"]


def test_doctor_hears_when_the_memory_limit_fails(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "apply_limits", lambda: False)
    monkeypatch.setattr(worker, "confine", lambda: "landlock v6, no sockets")
    assert worker.main(["confinement"]) == 0
    how = capsys.readouterr().out.strip()
    assert how == "unconfined: memory limit refused"
    assert not worker.fully_confined(how)


@pytest.mark.parametrize(
    "how", ["unconfined: Landlock unavailable", "landlock v3, no sockets", "unconfined: not Linux"]
)
def test_the_worker_refuses_to_read_a_pdf_it_cannot_confine(
    deck: Path,
    how: str,
    no_limits: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Security audit, 6 November 2026: the worker used to carry on unconfined.
    monkeypatch.setattr(worker, "confine", lambda: how)
    assert worker.main(["inspect", _fd(deck)]) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "unconfined"}
    assert no_limits == []  # the file was never parsed


def test_unconfined_rendering_needs_the_operator_to_ask_for_it(
    deck: Path,
    no_limits: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "confine", lambda: "unconfined: not Linux")
    assert worker.main([worker.ALLOW_UNCONFINED, "inspect", _fd(deck)]) == 0
    assert no_limits == ["parsed"]


def test_a_fully_confined_worker_reads_the_pdf(
    deck: Path, no_limits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "confine", lambda: "landlock v4, no sockets")
    assert worker.main(["inspect", _fd(deck)]) == 0
    assert no_limits == ["parsed"]


def test_snug_passes_the_opt_in_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    from django.conf import settings

    for allowed in (False, True):
        config = dataclasses.replace(settings.CONFIG, unsafe_unconfined_rendering=allowed)
        monkeypatch.setattr(settings, "CONFIG", config)
        argv = sandbox.command("inspect", "3")
        assert (worker.ALLOW_UNCONFINED in argv) is allowed
        assert argv[-2:] == ["inspect", "3"]


def test_the_launcher_works_without_django_and_never_opts_in() -> None:
    # The image smoke test calls the sandbox with no settings loaded. Unknown
    # settings must mean "confined", never an error and never the opt-in.
    code = "from snug.rendering import sandbox; print(sandbox.command('inspect', '3'))"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "SNUG_"))}
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "'inspect', '3']" in result.stdout
    assert worker.ALLOW_UNCONFINED not in result.stdout


def test_an_unconfined_refusal_is_explained() -> None:
    message = str(sandbox.WorkerError("unconfined"))
    assert message == (
        "This server cannot open documents safely. It needs Linux 6.7 or later, with Landlock, "
        "seccomp and a memory limit. Ask whoever runs the server."
    )


# ---------------------------------------------------------------- in-process, for coverage


def _fd(path: Path) -> str:
    return str(os.open(path, os.O_RDONLY))


def test_inspect_in_process(deck: Path) -> None:
    assert worker.inspect(deck.read_bytes()) == {"pages": 2, "sizes": [[960, 540], [595, 842]]}


def test_main_inspect_and_render_in_process(
    deck: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    assert worker.main(["inspect", _fd(deck)], confined=False) == 0
    assert json.loads(capsysbinary.readouterr().out)["pages"] == 2
    assert worker.main(["render", _fd(deck), "2", "800"], confined=False) == 0
    assert size_of(capsysbinary.readouterr().out)[0] in (800, 801)


@pytest.mark.parametrize("content", [b"not a pdf at all", b"%PDF-1.7\n" + b"x" * 500])
def test_main_reports_unreadable_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes
) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(content)
    assert worker.main(["inspect", _fd(bad)], confined=False) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "unreadable"}


def test_main_reports_missing_pages(deck: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert worker.main(["render", _fd(deck), "5", "800"], confined=False) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "no_such_page"}


def test_main_reports_memory_errors(
    deck: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def exhausted(data: bytes) -> dict[str, object]:
        raise MemoryError

    monkeypatch.setattr(worker, "inspect", exhausted)
    assert worker.main(["inspect", _fd(deck)], confined=False) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "memory"}


def test_main_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert worker.main(["nonsense"], confined=False) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "usage"}


def test_too_many_pages_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "MAX_PAGES", 3)
    with pytest.raises(worker._Refused) as caught:
        worker.inspect(make_pdf(*[(100, 100)] * 4))
    assert caught.value.code == "too_many_pages"


def test_gunicorn_master_makes_itself_undumpable(monkeypatch: pytest.MonkeyPatch) -> None:
    from snug import gunicorn_conf

    calls: list[bool] = []

    def spy() -> bool:
        calls.append(True)
        return True

    monkeypatch.setattr(gunicorn_conf, "make_undumpable", spy)
    gunicorn_conf.on_starting(None)
    assert calls == [True]


def test_a_flood_of_output_is_cut_off(deck: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A compromised worker streaming endlessly must not fill Snug's memory.
    flood = "import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)"
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", flood])
    started = time.monotonic()
    with pytest.raises(sandbox.WorkerError) as caught:
        sandbox.run(deck, "render", "1", "1600", timeout=30, limit=1024 * 1024)
    assert caught.value.code == "unreadable"
    assert time.monotonic() - started < 10


def test_a_silent_hang_is_cut_off(deck: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = "import sys, time\nsys.stdout.write('x'); sys.stdout.flush(); time.sleep(30)"
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", script])
    with pytest.raises(sandbox.WorkerError, match="took too long"):
        sandbox.run(deck, "inspect", timeout=0.5)


def test_children_left_behind_are_killed(
    deck: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A worker that (somehow) started a child, then exited normally.
    marker = tmp_path / "child.pid"
    script = (
        "import json, os, sys, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.close(1)\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"open({str(marker)!r}, 'w').write(str(pid))\n"
        "sys.stdout.write(json.dumps({'pages': 1, 'sizes': [[1, 1]]}))\n"
    )
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", script])
    assert sandbox.inspect(deck)["pages"] == 1
    child = int(marker.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("the worker's child outlived it")


@linux_only
def test_a_limited_worker_cannot_start_processes() -> None:
    probe = (
        "from snug.rendering import worker; worker.apply_limits()\n"
        "import os\n"
        "try:\n    os.fork()\n    print('forked')\n"
        "except BlockingIOError:\n    print('refused')\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=30, check=False
    ).stdout
    assert out.strip() == "refused"
