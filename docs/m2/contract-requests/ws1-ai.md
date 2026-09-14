# Contract requests: ws1-ai

No frozen-file changes were needed; ws1-ai was implemented entirely inside its owned files against the Phase 0
contracts (`sed/ai/contract.py`, `sed/ai/cli.py` signatures, schema 003, `snapshot.AiParts`/`format_provenance_line`).

Non-blocking notes for the integrator (no action required for M2):
- `ai_batch` has no per-batch low-confidence column, so `sed ai ingest` on an unchanged file reports
  `low_confidence: 0`. Run-level counts in `finish-run` are computed from the labels and are exact.
- `RunPlan.plan` carries one extra integer key, `claimed`, next to items/batches/est_agents/max_chars_per_batch/
  longest_line_chars (allowed by `dict[str, int]`); I1 uses it for the claims count check.
