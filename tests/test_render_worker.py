"""The document worker runs apart from Snug: limited, confined and given nothing secret."""

import io
import json
import os
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


# ---------------------------------------------------------------- failing closed


@pytest.fixture
def no_limits(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """main() with confinement on, but without limiting the test process itself."""
    parsed: list[str] = []

    def inspect(data: bytes) -> dict[str, int]:
        parsed.append("parsed")
        return {"pages": 1}

    monkeypatch.setattr(worker, "apply_limits", lambda: None)
    monkeypatch.setattr(worker, "inspect", inspect)
    return parsed


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
        "This server cannot open documents safely. It needs Linux 6.7 or later, with Landlock. "
        "Ask whoever runs the server."
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
