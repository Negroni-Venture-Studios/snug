"""The seccomp filter that confines Snug's document worker, and its LibreOffice converter.

Standard library only, so the converter's image can carry this one module
without the rest of Snug. The filter is built as BPF bytecode by hand: a
list of instructions and labels, assembled with forward jumps only.
"""

import struct

# The syscalls the seccomp filter refuses outright. io_uring can open sockets
# without calling socket(), so it goes too. The rest reach into another
# process: tracing it, reading or writing its memory, signalling it through a
# pidfd, or changing its priority, scheduling or memory placement. The worker
# does none of these to itself either.
_REFUSED = (
    "socket",
    "io_uring_setup",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "pidfd_open",
    "pidfd_getfd",
    "pidfd_send_signal",
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
)
# The syscalls that send a signal: allowed only to the worker itself.
_SIGNALLING = ("kill", "tkill", "tgkill", "rt_sigqueueinfo", "rt_tgsigqueueinfo")
# Each architecture's audit value and syscall numbers.
_SECCOMP_ARCHES = {
    "x86_64": (
        0xC000003E,
        {
            "socket": 41,
            "kill": 62,
            "ptrace": 101,
            "rt_sigqueueinfo": 129,
            "setpriority": 141,
            "sched_setparam": 142,
            "sched_setscheduler": 144,
            "tkill": 200,
            "sched_setaffinity": 203,
            "tgkill": 234,
            "ioprio_set": 251,
            "migrate_pages": 256,
            "move_pages": 279,
            "rt_tgsigqueueinfo": 297,
            "prlimit64": 302,
            "process_vm_readv": 310,
            "process_vm_writev": 311,
            "kcmp": 312,
            "pidfd_send_signal": 424,
            "io_uring_setup": 425,
            "pidfd_open": 434,
            "pidfd_getfd": 438,
            "process_madvise": 440,
            "sched_setattr": 314,
            "process_mrelease": 448,
        },
    ),
    "aarch64": (
        0xC00000B7,
        {
            "ioprio_set": 30,
            "ptrace": 117,
            "sched_setparam": 118,
            "sched_setscheduler": 119,
            "sched_setaffinity": 122,
            "kill": 129,
            "tkill": 130,
            "tgkill": 131,
            "rt_sigqueueinfo": 138,
            "setpriority": 140,
            "socket": 198,
            "migrate_pages": 238,
            "move_pages": 239,
            "rt_tgsigqueueinfo": 240,
            "prlimit64": 261,
            "process_vm_readv": 270,
            "process_vm_writev": 271,
            "kcmp": 272,
            "pidfd_send_signal": 424,
            "io_uring_setup": 425,
            "pidfd_open": 434,
            "pidfd_getfd": 438,
            "process_madvise": 440,
            "sched_setattr": 274,
            "process_mrelease": 448,
        },
    ),
}

# A BPF instruction: its code, its constant, and the labels it jumps to if
# its test is true or false (None for the next instruction).
_Instruction = tuple[int, int, str | None, str | None]


def _assemble(lines: list[str | _Instruction]) -> bytes:
    """BPF bytecode from instructions and the labels between them. Jumps go forward only."""
    labels: dict[str, int] = {}
    instructions: list[_Instruction] = []
    for line in lines:
        if isinstance(line, str):
            labels[line] = len(instructions)
        else:
            instructions.append(line)

    def offset(index: int, label: str | None) -> int:
        return 0 if label is None else labels[label] - index - 1

    return b"".join(
        struct.pack("HBBI", code, offset(index, true), offset(index, false), k)
        for index, (code, k, true, false) in enumerate(instructions)
    )


def seccomp_filter(machine: str, pid: int, *, unix_sockets: bool = False) -> bytes | None:
    """A seccomp program for the process `pid`. Refused calls get EPERM.

    It refuses to create sockets: Landlock's network rules cover TCP only, so
    without this a compromised worker could still send UDP. It refuses every
    call in _REFUSED, so the worker cannot trace, re-prioritise or read the
    memory of another process. It lets the worker send a signal only to
    itself (abort() does), never to another process or process group, and
    change only its own limits. Landlock scopes signals only from ABI 6. Any
    other architecture or ABI (32-bit compatibility calls, x32) is killed
    outright rather than filtered.

    `unix_sockets` is for the LibreOffice converter, which may talk to
    itself over a Unix socket: socket() is then allowed for AF_UNIX alone,
    and refused for every network family. The worker never gets it.
    """
    if machine not in _SECCOMP_ARCHES:
        return None
    arch, numbers = _SECCOMP_ARCHES[machine]
    load, jump_eq, jump_ge, ret = 0x20, 0x15, 0x35, 0x06  # BPF_LD|W|ABS, JEQ, JGE, RET
    allow, eperm, kill = 0x7FFF0000, 0x00050000 | 1, 0x80000000
    # The target of a signal or of prlimit64: the low 32 bits of its first
    # argument, which is all of a pid_t.
    target = 16
    af_unix = 1

    lines: list[str | _Instruction] = [
        (load, 4, None, None),  # the architecture
        (jump_eq, arch, None, "kill"),
        (load, 0, None, None),  # the syscall number
    ]
    if machine == "x86_64":
        lines.append((jump_ge, 0x40000000, "refuse", None))  # x32 calls carry this bit
    lines += [
        (jump_eq, numbers[name], "socket" if name == "socket" and unix_sockets else "refuse", None)
        for name in _REFUSED
    ]
    lines += [(jump_eq, numbers[name], "signal", None) for name in _SIGNALLING]
    lines += [
        (jump_eq, numbers["prlimit64"], "limits", None),
        (ret, allow, None, None),  # anything else
        "socket",
        (load, target, None, None),  # the family: socket's first argument
        (jump_eq, af_unix, "allow", "refuse"),
        "signal",
        (load, target, None, None),
        (jump_eq, pid, "allow", "refuse"),  # the worker itself; never 0, its process group
        "limits",
        (load, target, None, None),
        (jump_eq, 0, "allow", None),  # 0 is the caller: glibc's getrlimit and setrlimit
        (jump_eq, pid, "allow", "refuse"),
        "allow",
        (ret, allow, None, None),
        "refuse",
        (ret, eperm, None, None),
        "kill",
        (ret, kill, None, None),
    ]
    return _assemble(lines)
