"""Process hardening for Snug's own processes."""

import sys


def is_linux() -> bool:
    # A function rather than an inline check, so type checkers on macOS and
    # Linux alike treat both branches as reachable.
    return sys.platform == "linux"


def make_undumpable() -> bool:
    """Mark this process non-dumpable (Linux).

    Its /proc entries, including its environment with the secret key, become
    unreadable to other processes of the same user, such as a PDF worker that
    a hostile file had taken over. It also turns off core dumps. Forked
    children inherit the flag; a new program started with exec does not.
    Returns True if the flag was set.
    """
    if not is_linux():
        return False
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    return bool(libc.prctl(4, 0, 0, 0, 0) == 0)  # PR_SET_DUMPABLE, 0
