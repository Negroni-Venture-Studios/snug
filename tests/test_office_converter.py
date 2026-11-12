"""The LibreOffice converter: its seccomp filter, and the supervisor that runs one conversion.

Decided 12 November 2026: LibreOffice runs in a container of its own, under
gVisor, with no network, converting one file and exiting so that Docker
starts a fresh container for the next. Inside it, the process that runs
LibreOffice is confined by a seccomp filter built by the same code as the
document worker's, allowing Unix sockets alone.
"""

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from snug.office import supervisor
from snug.rendering import seccomp
from tests.test_render_worker import (
    SECCOMP_ALLOW,
    SECCOMP_EPERM,
    SYSCALLS,
    WORKER_PID,
    _seccomp,
)

AF_UNIX, AF_INET, AF_INET6, AF_NETLINK, AF_PACKET = 1, 2, 10, 16, 17


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_converter_may_open_unix_sockets_and_no_others(machine: str) -> None:
    arch, numbers = SYSCALLS[machine]
    program = seccomp.seccomp_filter(machine, WORKER_PID, unix_sockets=True)
    assert program is not None
    assert _seccomp(program, arch, numbers["socket"], AF_UNIX, 1) == SECCOMP_ALLOW
    for family in (AF_INET, AF_INET6, AF_NETLINK, AF_PACKET, 0, 0x101):
        assert _seccomp(program, arch, numbers["socket"], family, 1) == SECCOMP_EPERM, family


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_converter_still_cannot_reach_into_other_processes(machine: str) -> None:
    arch, numbers = SYSCALLS[machine]
    program = seccomp.seccomp_filter(machine, WORKER_PID, unix_sockets=True)
    assert program is not None
    for name in ("io_uring_setup", "ptrace", "process_vm_readv", "pidfd_open", "kcmp"):
        assert _seccomp(program, arch, numbers[name], WORKER_PID + 1) == SECCOMP_EPERM, name
    assert _seccomp(program, arch, numbers["kill"], WORKER_PID + 1, 9) == SECCOMP_EPERM


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_document_worker_still_refuses_unix_sockets(machine: str) -> None:
    """The worker's filter is unchanged: it opens no socket of any kind."""
    arch, numbers = SYSCALLS[machine]
    program = seccomp.seccomp_filter(machine, WORKER_PID)
    assert program is not None
    assert _seccomp(program, arch, numbers["socket"], AF_UNIX, 1) == SECCOMP_EPERM


# ---------------------------------------------------------------- the supervisor

FAKE_SOFFICE = """
import json, os, sys, time
args = sys.argv[1:]
source = args[-1]
out = args[args.index("--outdir") + 1]
data = open(source, "rb").read()
if data == b"slow":
    time.sleep(60)
if data == b"fail":
    sys.exit(1)
body = b"junk" if data == b"junk" else b"%PDF-1.7\\n"
record = {"args": args, "env": dict(os.environ), "cwd": os.getcwd()}
with open(os.path.join(out, "input.pdf"), "wb") as made:
    made.write(body + json.dumps(record).encode())
"""
TOKEN = "Zk3pR8vQ2mN5xW7yT4aB"


@pytest.fixture
def soffice(tmp_path: Path) -> str:
    """A stand-in for soffice.bin: a script that answers as LibreOffice would."""
    script = tmp_path / "fake_soffice.py"
    script.write_text(FAKE_SOFFICE)
    launcher = tmp_path / "soffice"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    launcher.chmod(0o755)
    return str(launcher)


@pytest.fixture
def exchange(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    inbox, outbox, work, template = (tmp_path / n for n in ("inbox", "outbox", "work", "profile"))
    for folder in (inbox, outbox, work, template):
        folder.mkdir()
    (template / "registrymodifications.xcu").write_text("<hardened/>")
    return inbox, outbox, work, template


def put_job(inbox: Path, token: str, kind: str, data: bytes) -> None:
    folder = inbox / token
    folder.mkdir()
    (folder / f"input.{kind}").write_bytes(data)
    (folder / "job.json").write_text(json.dumps({"kind": kind}))


def made_by(pdf: bytes | str) -> dict[str, Any]:
    assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF-1.7\n"), pdf
    record: dict[str, Any] = json.loads(pdf.split(b"\n", 1)[1])
    return record


def test_the_next_job_is_the_first_unanswered_one(exchange: tuple[Path, ...]) -> None:
    inbox, outbox, _, _ = exchange
    put_job(inbox, "A" * 20, "docx", b"x")
    put_job(inbox, "B" * 20, "pptx", b"x")
    (outbox / f"{'A' * 20}.pdf").write_bytes(b"%PDF-")
    (inbox / "not a token").mkdir()
    put_job(inbox, "C" * 20, "exe", b"x")  # an unknown kind sorts last and is skipped
    (inbox / ("0" * 20)).mkdir()  # a folder still being written: no job.json yet
    job = supervisor.next_job(inbox, outbox)
    assert job is not None and (job.token, job.kind) == ("B" * 20, "pptx")


@pytest.mark.parametrize(
    ("kind", "infilter", "export"),
    [
        ("docx", "MS Word 2007 XML", "pdf:writer_pdf_Export"),
        ("pptx", "Impress MS PowerPoint 2007 XML", "pdf:impress_pdf_Export"),
        ("xlsx", "Calc MS Excel 2007 XML", "pdf:calc_pdf_Export:"),
    ],
)
def test_libreoffice_is_given_the_filter_for_the_kind_and_a_fresh_profile(
    exchange: tuple[Path, ...],
    soffice: str,
    kind: str,
    infilter: str,
    export: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SNUG_SECRET_KEY", "never-for-libreoffice")
    inbox, _, work, template = exchange
    put_job(inbox, TOKEN, kind, b"a real file")
    job = supervisor.next_job(inbox, exchange[1])
    assert job is not None
    record = made_by(
        supervisor.convert(job, work_root=work, template=template, soffice=soffice, confine=None)
    )
    args = record["args"]
    assert f"--infilter={infilter}" in args
    assert args[args.index("--convert-to") + 1].startswith(export)
    assert "--norestore" in args and "--headless" in args
    profile = next(a for a in args if a.startswith("-env:UserInstallation=file://"))
    assert profile.removeprefix("-env:UserInstallation=file://").startswith(str(work))
    # Nothing from the supervisor's own environment reaches LibreOffice.
    assert "SNUG_SECRET_KEY" not in record["env"]
    assert record["env"]["PATH"] == "/usr/bin:/bin"
    assert record["env"]["SAL_USE_VCLPLUGIN"] == "svp"
    assert record["env"]["HOME"].startswith(str(work))
    assert list(work.iterdir()) == []  # the job's folder is gone


@pytest.mark.parametrize(("data", "code"), [(b"fail", "unreadable"), (b"junk", "unreadable")])
def test_a_failed_conversion_says_so(
    exchange: tuple[Path, ...], soffice: str, data: bytes, code: str
) -> None:
    inbox, outbox, work, template = exchange
    put_job(inbox, TOKEN, "docx", data)
    job = supervisor.next_job(inbox, outbox)
    assert job is not None
    result = supervisor.convert(
        job, work_root=work, template=template, soffice=soffice, confine=None
    )
    assert result == code


def test_a_conversion_that_runs_too_long_is_killed(
    exchange: tuple[Path, ...], soffice: str
) -> None:
    inbox, outbox, work, template = exchange
    put_job(inbox, TOKEN, "pptx", b"slow")
    job = supervisor.next_job(inbox, outbox)
    assert job is not None
    began = time.monotonic()
    result = supervisor.convert(
        job, work_root=work, template=template, soffice=soffice, confine=None, deadline=1
    )
    assert result == "timeout"
    assert time.monotonic() - began < 15


def test_one_job_is_answered_then_the_supervisor_exits(
    exchange: tuple[Path, ...], soffice: str, tmp_path: Path
) -> None:
    inbox, outbox, work, template = exchange
    facts = tmp_path / "snug-office.json"
    facts.write_text(json.dumps({"libreoffice": "25.2.3.2", "built": "2026-11-12"}))
    put_job(inbox, TOKEN, "docx", b"a real file")
    put_job(inbox, "Y" * 20, "docx", b"the next one waits for a fresh container")
    code = supervisor.serve(
        inbox=inbox,
        outbox=outbox,
        work_root=work,
        template=template,
        facts_file=facts,
        soffice=soffice,
        confine=None,
        min_lifetime=0,
        poll=0.01,
    )
    assert code == 0
    answered = [p for p in outbox.iterdir() if p.suffix == ".pdf"]
    assert len(answered) == 1  # one conversion per container
    made_by(answered[0].read_bytes())
    state = json.loads((outbox / "status.json").read_text())
    assert (state["libreoffice"], state["built"]) == ("25.2.3.2", "2026-11-12")


def test_an_error_is_answered_as_json(exchange: tuple[Path, ...]) -> None:
    _, outbox, _, _ = exchange
    supervisor.publish(outbox, TOKEN, "timeout")
    assert json.loads((outbox / f"{TOKEN}.error").read_text()) == {"error": "timeout"}
    assert not list(outbox.glob(".*"))  # nothing half-written left behind


def test_waiting_with_nothing_to_do_still_reports_status(
    exchange: tuple[Path, ...], soffice: str, tmp_path: Path
) -> None:
    inbox, outbox, work, template = exchange
    code = supervisor.serve(
        inbox=inbox,
        outbox=outbox,
        work_root=work,
        template=template,
        facts_file=tmp_path / "missing.json",
        soffice=soffice,
        confine=None,
        poll=0.01,
        patience=0.05,
    )
    assert code == 1
    assert json.loads((outbox / "status.json").read_text())["libreoffice"] == ""


def test_if_libreoffice_cannot_be_confined_the_file_is_not_converted(
    exchange: tuple[Path, ...], soffice: str
) -> None:
    """Fail closed, as the document worker does: no filter, no conversion."""
    inbox, outbox, work, template = exchange
    put_job(inbox, TOKEN, "docx", b"a real file")
    job = supervisor.next_job(inbox, outbox)
    assert job is not None

    def refuse() -> None:
        raise OSError("seccomp refused")

    result = supervisor.convert(
        job, work_root=work, template=template, soffice=soffice, confine=refuse
    )
    assert result == "unconfined"
