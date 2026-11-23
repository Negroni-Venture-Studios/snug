"""Run LibreOffice in a view of its container without the shared folders (issue #205).

    python3 -I -m snug.office.view WORK -- PROGRAM ARG...

LibreOffice runs as the supervisor's own user, so in the container's own
view it could list the inbox, write an answer to the outbox while still
running, and be handed the next file. This builds it a view of its own,
then runs PROGRAM in it:

- new user, mount, and process namespaces, mapping only the supervisor's
  own user and group, so nothing gains a privilege outside them;
- a root of its own on a tmpfs, holding read-only binds of /usr and /etc,
  fontconfig's cache, the device files LibreOffice opens, the job's folder
  WORK at its own path, read-write, and a /proc of its own;
- the container's root detached, so the view has no path back to /exchange.

PROGRAM is the first process in the new process namespace, sees no other
process, and when it exits the kernel ends anything it left. It gets the
supervisor's limits and seccomp filter (`supervisor.confine_child`) and no
capabilities: execve drops those the namespace gave, since it runs as the
supervisor's user rather than root.

gVisor, which the converter's container runs under, allows user namespaces
without privileges. If the view cannot be built, PROGRAM never runs and
this exits with UNVIEWED, which the supervisor answers as `unconfined`.
Standard library only, like the supervisor.
"""

import contextlib
import ctypes
import os
import re
import sys
import tempfile
from pathlib import Path

# The exit code when the view, or the limits and filter inside it, could not be applied.
UNVIEWED = 70

# What the view holds of the container, read-only, as well as WORK.
SYSTEM = ("/usr", "/etc", "/var/cache/fontconfig")
DEVICES = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
# The merged-/usr links: /bin is /usr/bin, and so on.
LINKS = ("/bin", "/lib", "/lib64", "/sbin")

MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 32, 4096, 16384, 1 << 18
MS_NOATIME, MS_NODIRATIME, MS_RELATIME = 1024, 2048, 1 << 21
# statvfs's flags for a mount, and the mount flags they stand for. A bind
# inherits them, and in a user namespace may not clear them, so each
# remount carries them over.
_KEPT = {
    1: MS_RDONLY,
    2: MS_NOSUID,
    4: MS_NODEV,
    8: MS_NOEXEC,
    1024: MS_NOATIME,
    2048: MS_NODIRATIME,
    4096: MS_RELATIME,
}
MNT_DETACH = 2
CLONE_NEWNS, CLONE_NEWUSER, CLONE_NEWPID = 0x00020000, 0x10000000, 0x20000000
PIVOT_ROOT = {"x86_64": 155, "aarch64": 41}


class Unviewed(Exception):
    """The view could not be built."""


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def _encoded(text: str | None) -> bytes | None:
    return None if text is None else text.encode()


def _mount(
    libc: ctypes.CDLL,
    source: str | None,
    target: str,
    kind: str | None,
    flags: int,
    data: str | None = None,
) -> None:
    result = libc.mount(_encoded(source), _encoded(target), _encoded(kind), flags, _encoded(data))
    if result != 0:
        raise Unviewed(f"mount {target}: {os.strerror(ctypes.get_errno())}")


def _remount(libc: ctypes.CDLL, target: str, *, writable: bool) -> None:
    """Remount the bind at TARGET with no set-user-id or device files, keeping its own flags."""
    flags = MS_BIND | MS_REMOUNT | MS_NOSUID | MS_NODEV | (0 if writable else MS_RDONLY)
    held = os.statvfs(target).f_flag
    flags |= sum(flag for bit, flag in _KEPT.items() if held & bit)
    _mount(libc, None, target, None, flags)


def _mounts_under(target: str) -> list[str]:
    """The mount points below TARGET, from this namespace's mount table, outermost first."""
    found = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        # The fifth field; mountinfo writes a space, tab, newline, or backslash as \ooo.
        point = re.sub(r"\\([0-7]{3})", lambda octal: chr(int(octal.group(1), 8)), line.split()[4])
        if point.startswith(target + "/"):
            found.append(point)
    return sorted(set(found), key=len)


def _bind(libc: ctypes.CDLL, source: str, target: str, *, writable: bool = False) -> None:
    """Bind SOURCE at TARGET, read-only unless writable, with no set-user-id or device files.

    The bind takes the mounts below SOURCE with it, such as the files Docker
    mounts in /etc, and each is remounted read-only too, rather than relying
    on Docker to have mounted them so.
    """
    _mount(libc, source, target, None, MS_BIND | MS_REC)
    _remount(libc, target, writable=writable)
    if not writable:
        for below in _mounts_under(target):
            _remount(libc, below, writable=False)


def build(work: Path) -> None:  # pragma: no cover - needs user namespaces, as under gVisor
    """Make this process's view: namespaces, a root of its own, and the old root gone.

    Coverage cannot see it: CI's runners refuse user namespaces, so
    tests/test_office_view.py runs it under gVisor, in the converter's job.
    """
    unshare = getattr(os, "unshare", None)  # Linux only
    if unshare is None or os.uname().machine not in PIVOT_ROOT:
        raise Unviewed("no namespaces here")
    libc = _libc()
    uid, gid = os.getuid(), os.getgid()
    try:
        unshare(CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWPID)
    except OSError as error:
        raise Unviewed(f"unshare: {error}") from error
    Path("/proc/self/setgroups").write_text("deny")
    Path("/proc/self/uid_map").write_text(f"{uid} {uid} 1")
    Path("/proc/self/gid_map").write_text(f"{gid} {gid} 1")
    _mount(libc, None, "/", None, MS_REC | MS_PRIVATE)  # nothing here reaches the container

    root = tempfile.mkdtemp(prefix=".view-", dir="/tmp")
    _mount(libc, "view", root, "tmpfs", MS_NOSUID | MS_NODEV, "size=1m,mode=0755")
    for folder in SYSTEM:
        if Path(folder).is_dir():
            Path(root + folder).mkdir(parents=True)
            _bind(libc, folder, root + folder)
    for link in LINKS:
        if Path(link).is_symlink():
            Path(root + link).symlink_to(Path(link).readlink())
    Path(root + "/dev").mkdir()
    for device in DEVICES:
        if Path(device).exists():
            Path(root + device).touch()
            _mount(libc, device, root + device, None, MS_BIND)  # a device, so not nodev
    # A /tmp of its own: LibreOffice puts its single-instance pipe there,
    # whatever TMPDIR says. Empty, and gone with the namespace.
    private_tmp = root + "/tmp"  # noqa: S108 - the view's own, on a tmpfs of its own
    Path(private_tmp).mkdir()
    _mount(libc, "tmp", private_tmp, "tmpfs", MS_NOSUID | MS_NODEV, "size=16m,mode=1777")
    Path(root + str(work)).mkdir(parents=True, exist_ok=True)
    _bind(libc, str(work), root + str(work), writable=True)
    Path(root + "/proc").mkdir()
    Path(root + "/.old").mkdir()
    if libc.syscall(PIVOT_ROOT[os.uname().machine], root.encode(), (root + "/.old").encode()) != 0:
        raise Unviewed(f"pivot_root: {os.strerror(ctypes.get_errno())}")
    os.chdir("/")
    if libc.umount2(b"/.old", MNT_DETACH) != 0:
        raise Unviewed(f"detaching the old root: {os.strerror(ctypes.get_errno())}")
    Path("/.old").rmdir()
    # The view's own root, a tmpfs, read-only now that everything is in place.
    _mount(libc, None, "/", None, MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV)


def _first_process(work: Path, program: list[str]) -> None:  # pragma: no cover - after fork
    """In the new process namespace: its own /proc, the limits and filter, then PROGRAM."""
    try:
        _mount(_libc(), "proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC)
        from snug.office import supervisor

        supervisor.confine_child()
        os.chdir(work)
        os.execv(program[0], program)  # noqa: S606 - the supervisor's own command line
    except BaseException as error:  # noqa: BLE001 - whatever stopped it, nothing ran
        with contextlib.suppress(OSError):
            os.write(2, f"snug.office.view: {error}\n".encode())
    os._exit(UNVIEWED)


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: python3 -I -m snug.office.view WORK -- PROGRAM ARG...", file=sys.stderr)
        return UNVIEWED
    work, program = Path(argv[0]), argv[2:]
    # Imported before the view: afterwards only the system's own files remain.
    from snug.office import supervisor  # noqa: F401

    try:
        build(work)
    except (Unviewed, OSError) as error:
        print(f"snug.office.view: {error}", file=sys.stderr)
        return UNVIEWED
    child = os.fork()
    if child == 0:  # pragma: no cover - the first process in the new namespace
        _first_process(work, program)
    status = os.waitpid(child, 0)[1]
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


if __name__ == "__main__":  # pragma: no cover - run by the supervisor
    sys.exit(main(sys.argv[1:]))
