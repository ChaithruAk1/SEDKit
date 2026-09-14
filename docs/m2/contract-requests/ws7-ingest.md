# Contract requests: ws7-ingest

## 1. Remove `ingest/loader.py` from the core-boundary allowlist (bring I3 forward)

- **File:** `tests/platform/test_core_boundaries.py` (frozen)
- **Change:** delete the `"ingest/loader.py",  # registry-driven ingest lands with ws7-ingest` entry from `ALLOWLIST`,
  leaving only `"cli.py"`.
- **Reason:** the workstream deliverable makes `src/sed/ingest/loader.py` import nothing from `sed.ingest.targets` or
  `sed.modules.ops` (it reaches targets and hooks only through `sed.modules.ingest_targets` / `ingest_hooks`;
  enforced by `tests/platform/ingest/test_ingest_boundaries.py`). As soon as that is true, the frozen guard
  `test_allowlist_entries_still_need_it` fails on this branch with:

  ```
  AssertionError: remove from ALLOWLIST: ['ingest/loader.py']
  ```

  The two frozen requirements cannot both hold on `m2/ws7-ingest`: either loader.py keeps a forbidden import (which
  breaks the deliverable and the workstream's own boundary test) or the stale-allowlist guard fails. No workaround is
  possible inside ws7-owned files without faking a forbidden import or patching the frozen test at runtime, so neither
  was done. This is exactly integration step **I3**; landing it as a `contract:` commit (or at merge time) makes
  `scripts/ci.py` fully green for this branch. With the entry removed, `test_core_boundaries.py` passes against this
  branch (loader.py has no violations).
