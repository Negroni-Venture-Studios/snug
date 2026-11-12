"""The LibreOffice converter: its seccomp filter, and the supervisor that runs one conversion.

Decided 12 November 2026: LibreOffice runs in a container of its own, under
gVisor, with no network, converting one file and exiting so that Docker
starts a fresh container for the next. Inside it, the process that runs
LibreOffice is confined by a seccomp filter built by the same code as the
document worker's, allowing Unix sockets alone.
"""

import pytest

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
