"""The PDF worker runs apart from Snug, with limits and nothing secret."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from snug.rendering import sandbox, worker
from tests.pdfs import make_pdf


@pytest.fixture
def deck(tmp_path: Path) -> Path:
    path = tmp_path / "deck.pdf"
    path.write_bytes(make_pdf((960, 540), (595, 842)))
    return path


def test_inspect(deck: Path) -> None:
    assert sandbox.inspect(deck) == {"pages": 2, "sizes": [[960, 540], [595, 842]]}


def test_render_fits_the_width(deck: Path, tmp_path: Path) -> None:
    out = tmp_path / "1.webp"
    result = sandbox.render(deck, 1, 1600, out)
    assert result == {"width": 1600, "height": 900}
    with Image.open(out) as image:
        assert image.format == "WEBP"
        assert image.size == (1600, 900)
    assert not list(tmp_path.glob(".*.part"))


def test_very_tall_pages_are_capped(tmp_path: Path) -> None:
    pdf = tmp_path / "tall.pdf"
    pdf.write_bytes(make_pdf((10, 14000)))
    result = sandbox.render(pdf, 1, 1600, tmp_path / "t.webp")
    assert result["width"] * result["height"] <= worker.MAX_PIXELS
    assert max(result["width"], result["height"]) <= worker.MAX_SIDE


def test_missing_page(deck: Path, tmp_path: Path) -> None:
    with pytest.raises(sandbox.WorkerError, match="does not exist"):
        sandbox.render(deck, 9, 1600, tmp_path / "x.webp")


def test_bad_arguments(deck: Path) -> None:
    with pytest.raises(sandbox.WorkerError):
        sandbox.run("explode", str(deck), timeout=30)


def test_the_worker_gets_no_secrets() -> None:
    env = sandbox.worker_env()
    assert not [k for k in env if k.startswith(("SNUG_", "DJANGO_"))]
    assert set(env) == {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE"}


def test_the_worker_runs_isolated() -> None:
    argv = sandbox.command("inspect", "x.pdf")
    assert argv[:4] == [sys.executable, "-I", "-m", "snug.rendering.worker"]


def test_the_worker_limits_itself() -> None:
    probe = (
        "import resource, json; from snug.rendering import worker; worker.apply_limits(); "
        "print(json.dumps({n: resource.getrlimit(getattr(resource, n))[0] "
        "for n in ('RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NOFILE', 'RLIMIT_CORE', 'RLIMIT_AS')}))"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    limits = json.loads(out)
    assert limits["RLIMIT_CPU"] == worker.CPU_SECONDS
    assert limits["RLIMIT_FSIZE"] == worker.OUTPUT_BYTES
    assert limits["RLIMIT_NOFILE"] == worker.OPEN_FILES
    assert limits["RLIMIT_CORE"] == 0
    if sys.platform == "linux":
        assert limits["RLIMIT_AS"] == worker.MEMORY_BYTES


def test_timeouts_kill_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sandbox, "command", lambda *a: [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    with pytest.raises(sandbox.WorkerError, match="took too long"):
        sandbox.run("inspect", "x", timeout=0.5)


@pytest.mark.parametrize(
    ("returncode", "code"),
    [(-9, "memory"), (-24, "unreadable"), (-11, "unreadable")],
    ids=["killed", "cpu", "segfault"],
)
def test_crashes_become_errors(monkeypatch: pytest.MonkeyPatch, returncode: int, code: str) -> None:
    script = f"import os, signal; os.kill(os.getpid(), {-returncode})"
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", script])
    with pytest.raises(sandbox.WorkerError) as caught:
        sandbox.run("inspect", "x", timeout=30)
    assert caught.value.code == code


def test_garbage_output_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "command", lambda *a: [sys.executable, "-c", "print('hello')"])
    with pytest.raises(sandbox.WorkerError) as caught:
        sandbox.run("inspect", "x", timeout=30)
    assert caught.value.code == "unreadable"


def test_main_usage() -> None:
    assert worker.main(["nonsense"]) == 3


# In-process calls, so coverage sees the worker's own code. The subprocess
# tests above prove the same paths work across the process boundary.


def test_inspect_in_process(deck: Path) -> None:
    assert worker.inspect(str(deck)) == {"pages": 2, "sizes": [[960, 540], [595, 842]]}


def test_main_inspect_and_render_in_process(
    deck: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "apply_limits", lambda: None)  # keep the test process unlimited
    assert worker.main(["inspect", str(deck)]) == 0
    assert json.loads(capsys.readouterr().out)["pages"] == 2
    out = tmp_path / "p.webp"
    assert worker.main(["render", str(deck), "2", "800", str(out)]) == 0
    # pdfium rounds the pixel size up, so allow one pixel either way.
    assert abs(json.loads(capsys.readouterr().out)["width"] - 800) <= 1
    assert out.exists()


@pytest.mark.parametrize(
    ("content", "code"),
    [(b"not a pdf at all", "unreadable"), (b"%PDF-1.7\n" + b"x" * 500, "unreadable")],
)
def test_main_reports_unreadable_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    code: str,
) -> None:
    monkeypatch.setattr(worker, "apply_limits", lambda: None)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(content)
    assert worker.main(["inspect", str(bad)]) == 3
    assert json.loads(capsys.readouterr().out) == {"error": code}


def test_main_reports_missing_pages(
    deck: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "apply_limits", lambda: None)
    assert worker.main(["render", str(deck), "5", "800", str(tmp_path / "x.webp")]) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "no_such_page"}


def test_main_reports_memory_errors(
    deck: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def exhausted(path: str) -> dict[str, object]:
        raise MemoryError

    monkeypatch.setattr(worker, "apply_limits", lambda: None)
    monkeypatch.setattr(worker, "inspect", exhausted)
    assert worker.main(["inspect", str(deck)]) == 3
    assert json.loads(capsys.readouterr().out) == {"error": "memory"}


def test_too_many_pages_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "long.pdf"
    pdf.write_bytes(make_pdf(*[(100, 100)] * 4))
    monkeypatch.setattr(worker, "MAX_PAGES", 3)
    with pytest.raises(worker._Refused) as caught:
        worker.inspect(str(pdf))
    assert caught.value.code == "too_many_pages"
