"""Definitions of AI-derived facts (ws1-ai). Listed in `ai_derived_facts`, so `--ai none` drops them and their
definitions from every rendered report."""

from __future__ import annotations

AI_DEFINITIONS: dict[str, tuple[str, str]] = {
    "ai.category.labelled_pct": (
        "pct",
        "Share of incidents opened in the period that carry an approved AI app-owner category for their current "
        "content (label from an approved sed-triage-batch or manual run).",
    ),
    "ai.category.sample_accuracy_pct": (
        "pct",
        "Human-reviewed accuracy of the AI category on the random stratified review sample of the approved run that "
        "labelled the most of these incidents, weighted by stratum size.",
    ),
    "ai.category.sample_ci_low_pct": (
        "pct",
        "Lower bound of the Wilson 95% interval around the AI category sample accuracy (n = random sample size).",
    ),
    "ai.category.sample_ci_high_pct": (
        "pct",
        "Upper bound of the Wilson 95% interval around the AI category sample accuracy (n = random sample size).",
    ),
}
