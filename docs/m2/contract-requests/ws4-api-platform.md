# Contract requests: ws4-api-platform

## 1. `tests/platform/test_api_factory.py::test_unsafe_methods_need_the_token` asserts the Phase 0 stub

- **File:** `tests/platform/test_api_factory.py` (frozen, `tests/platform/*.py`).
- **Problem:** the test posts `{"kind": "vendor", "raw_value": "x", "target": "y"}` with a valid token and asserts
  `501` plus the exact `not_implemented` envelope of the Phase 0 stub. Once `POST /api/aliases` is implemented
  (a ws4 deliverable), the same request returns `422` with a `validation` envelope (`Unknown vendor target 'y'`),
  so this frozen test fails on the ws4 branch and cannot pass with any real implementation. No database write
  happens before that validation error, so the shared session `ops_profile` stays unchanged.
- **Requested change** (replace lines 39-48, the `with_token` block):

  ```python
  with_token = api_client(ops_profile.paths).post("/api/aliases", json=body)
  assert with_token.status_code == 422  # the token passed; the unknown target is a validation error
  assert with_token.json()["ok"] is False and with_token.json()["error"]["kind"] == "validation"
  ```

- **Reason:** the test's intent (unsafe methods need the token; a valid token gets past the middleware) is kept.
  Only the stub-specific status and body go.
- **Same pattern elsewhere (not ws4's to change, noted for the integrator):** `test_disabled_module_is_not_mounted`
  asserts `GET /api/ops/filters == 501` on line 93, which breaks when ws5-api-ops lands; `!= 404` keeps its intent.

## 2. FYI (not a frozen-file request): alias re-linking does not reach ticket `vendor_id` through assignment groups

- **Status:** fixed in the I9 review (ING-3): reresolve re-resolves assignment groups first; the xfail is now a
  normal test.

- **Observed on `ops_profile_rw`:** after `POST /api/aliases {"kind": "vendor", "raw_value": "NORDWIND MS",
  "target": "V001"}`, `reresolve` re-links the licence row and marks the unmapped value resolved. The tickets of
  group `NWD-FIN-L2` (whose `assignment_group.vendor_raw` is `NORDWIND MS`) keep `vendor_id = NULL`.
- **Cause:** `sed.ingest.loader.reresolve` (ws7-owned, behaviour-identical to M1) derives ticket vendors from the
  stored `assignment_group.vendor_id`. It never re-resolves `assignment_group.vendor_raw`, so a new vendor alias
  reaches those tickets only after the group register is imported again.
- **Suggested fix (ws7 or integrator):** in `reresolve`, re-resolve `assignment_group.vendor_raw` / `app_raw`
  first (update the rows and `Resolver.set_group`), then re-link tickets.
- **ws4 coverage:** `tests/platform/api/test_aliases_api.py` asserts the contract, licence and ticket (app) re-links
  that work today. The group-derived ticket vendor case is a non-strict `xfail` that reports XPASS once fixed.
