"""A stand-in for the LibreOffice converter's container, for tests of Snug's side."""

import json
from pathlib import Path

from tests.pdfs import make_pdf


class Converter:
    """A stand-in for the converter's container: it answers whatever job is in the inbox."""

    def __init__(self, root: Path) -> None:
        self.inbox, self.outbox = root / "inbox", root / "outbox"
        self.inbox.mkdir(parents=True)
        self.outbox.mkdir(parents=True)

    def jobs(self) -> list[Path]:
        return sorted(p for p in self.inbox.iterdir() if not p.name.startswith("."))

    def answer(self, pdf: bytes | None = None, *, error: str | None = None) -> str:
        (job,) = self.jobs()
        if error is not None:
            (self.outbox / f"{job.name}.error").write_text(json.dumps({"error": error}))
        else:
            (self.outbox / f"{job.name}.pdf").write_bytes(pdf or make_pdf(*[(720, 540)] * 3))
        return job.name
