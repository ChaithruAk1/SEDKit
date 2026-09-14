"""`sed serve`: run the API and dashboard on 127.0.0.1 with a per-launch token (ws4-api-platform)."""

from __future__ import annotations

from sed.errors import NotImplementedByWorkstream
from sed.paths import Paths


def run(paths: Paths, *, port: int = 8000, open_browser: bool = True, dev: bool = False) -> None:
    raise NotImplementedByWorkstream("ws4-api-platform")
