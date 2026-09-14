"""Weekly Application Operations Review snapshot builder."""

from __future__ import annotations

from typing import Any


def build(req: Any) -> Any:
    from sed.reports import snapshot

    return snapshot.weekly(req.conn, req.paths, req.settings, req.period)
