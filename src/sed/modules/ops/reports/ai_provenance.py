"""AI contributions to ops reports (ws1-ai): approved triage runs behind the weekly category breakdown.

Phase 0 stub: no AI facts or runs yet. The category breakdown is already declared AI-derived,
so `--ai none` excludes it.
"""

from __future__ import annotations

from sed.reports.snapshot import AiParts, SnapshotRequest


def weekly_ai(req: SnapshotRequest) -> AiParts:
    return AiParts({}, [], ("category_breakdown",), ())
