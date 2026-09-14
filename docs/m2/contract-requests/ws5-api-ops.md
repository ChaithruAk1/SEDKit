# Contract requests: ws5-api-ops

## 1. `tests/platform/test_api_factory.py::test_disabled_module_is_not_mounted` asserts the Phase 0 stub status

- **File:** `tests/platform/test_api_factory.py` (frozen, `tests/platform/*.py`), line 93.
- **Current:** `assert api_client(ops_profile.paths).get("/api/ops/filters").status_code == 501`
- **Requested change:** `assert api_client(ops_profile.paths).get("/api/ops/filters").status_code == 200`
  (or `!= 404` if the integrator prefers a status-agnostic mount check).
- **Reason:** the assertion checks that an enabled module's router is mounted, but it does so through the
  `NotImplementedByWorkstream` stub status. ws5-api-ops must implement `GET /api/ops/filters` (deliverable 1), which
  now returns 200 with `OpsFiltersOut`, so the frozen test fails on this branch and `scripts/ci.py` (pytest step) is
  red with exactly this one failure. The intent of the test (disabled module -> 404, enabled module -> mounted) is
  unchanged by the requested edit. No workaround exists inside ws5-owned files without keeping the stub.
- **Merge note:** apply together with the ws5-api-ops merge (I4); until then the failure is expected on `m2/ws5-api-ops`.
