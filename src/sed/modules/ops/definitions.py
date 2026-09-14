"""Metric and fact definitions published by the ops module (Definitions sheet, deck notes, /api/meta)."""

from __future__ import annotations

from sed.metrics import METRICS
from sed.modules.ops.ai.definitions import AI_DEFINITIONS
from sed.modules.ops.reports.definitions import REPORT_DEFINITIONS


def merge_unique(*sources: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for source in sources:
        for key, value in source.items():
            if key in out:
                raise ValueError(f"definition '{key}' declared twice")
            out[key] = value
    return out


DEFINITIONS = merge_unique(METRICS, REPORT_DEFINITIONS, AI_DEFINITIONS)
