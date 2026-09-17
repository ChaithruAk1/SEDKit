"""Fake human reviewer: verdicts for a `sed review sample` result, in the `--template` file format."""

from __future__ import annotations

from typing import Any


def sample_keys(sample: dict[str, Any]) -> list[str]:
    cards = list(sample.get("random", [])) + list(sample.get("lowest_confidence", []))
    return sorted({f"{c['item_id']}|{c['stage']}" for c in cards})


def verdicts_for(
    sample: dict[str, Any],
    *,
    incorrect_every: int = 10,
    skip: set[str] | None = None,
    correct_to: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verdict "correct" for every sampled item except every Nth key (sorted), which is marked incorrect.

    `correct_to` (e.g. {"category": "other", "subcategory": "other"}) adds the right label to incorrect verdicts, which
    approve-run then applies as manual corrections. Keys in `skip` get null (skipped, not recorded).
    """
    out: dict[str, Any] = {}
    for idx, key in enumerate(sample_keys(sample), start=1):
        if skip and key in skip:
            out[key] = None
        elif incorrect_every and idx % incorrect_every == 0:
            out[key] = {"verdict": "incorrect", **(correct_to or {})}
        else:
            out[key] = "correct"
    return out
