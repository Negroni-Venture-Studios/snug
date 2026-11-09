"""Snug's own calls out, to Google and to DB-IP, with proxies from the environment only.

urllib's default opener asks the system for proxy settings. On macOS it does
so through Apple's frameworks, which refuse to run in a process forked from
another, as gunicorn starts its workers, and kill it: the browser sees an
empty response. Proxies named in the environment (HTTPS_PROXY and the like)
still apply, as they do on a Linux server.
"""

import urllib.request
from typing import Any


def _opener() -> urllib.request.OpenerDirector:
    proxies = urllib.request.ProxyHandler(urllib.request.getproxies_environment())
    return urllib.request.build_opener(proxies)


def urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open a request, as urllib.request.urlopen does. Callers pass only fixed https URLs."""
    return _opener().open(request, timeout=timeout)
