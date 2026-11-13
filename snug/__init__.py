"""Snug: a self-hosted data room for early-stage founders."""


def __getattr__(name: str) -> str:
    # Read when asked, not on import: the document worker imports this package
    # for every page it renders or stamps, and reading the installed metadata
    # took half a second of each start on a one-CPU server.
    if name == "__version__":
        from importlib.metadata import version

        return version("snug")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
