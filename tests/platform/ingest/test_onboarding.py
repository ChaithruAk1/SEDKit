"""Onboarding real exports: file profile without personal values, draft mappings tried without saving, validated
mapping and config overrides that restore the previous file on failure, and the reconciliation command."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from sed.errors import ValidationFailed
from sed.ingest import onboarding
from sed.ingest.loader import ImportOptions, run_import
from sed.ingest.mapping import load_mapping

GROUP_HEADER = ["Group name", "Vendor", "Application", "Manager"]
GROUP_ROWS = [
    ["KEEL-MFG-L2", "V001", "APM1001000", "Anna Berg"],
    ["NWD-FIN-L2", "V002", "APM1002000", "Paul Martin"],
    ["IT-HR-L2", "", "APM1003000", "Berg, Anna"],
]


def _csv(path: Path, header: list[str], rows: list[list[Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _yaml(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_profile_shows_shapes_and_only_safe_categories(ops_profile, tmp_path):
    file = _csv(
        tmp_path / "export.csv",
        ["Number", "Opened", "Priority", "Assigned to", "Short description", "Contact"],
        [
            ["INC0012345", "03/08/2026 09:15:00", "2 - High", "Anna Berg", "Printer offline", "+33 6 12 34 56 78"],
            ["INC0012346", "04/08/2026 10:00:00", "3 - Moderate", "Paul Martin", "VPN drops", "x@example.com"],
        ],
    )
    profile = onboarding.profile_file(file, ops_profile.paths)
    cols = {c["column"]: c for c in profile["columns"]}
    assert profile["rows"] == 2 and profile["candidates"]
    assert cols["Opened"]["shapes"] == {"datetime D/M/YYYY HH:MM[:SS]": 2}
    assert cols["Number"]["shapes"] == {"code AAA9999999": 2}
    assert cols["Priority"]["categories"] == ["2 - High", "3 - Moderate"]
    assert cols["Assigned to"]["categories"] is None  # person header
    assert cols["Contact"]["categories"] is None  # person header and phone/email values
    text = json.dumps(profile)
    assert "Anna Berg" not in text and "Paul Martin" not in text and "example.com" not in text


def test_person_like_values_are_never_listed_even_under_a_neutral_header():
    assert onboarding._safe_values("Queue", ["Anna Berg", "Paul Martin"]) is None
    assert onboarding._safe_values("Queue", ["Berg, Anna"]) is None
    assert onboarding._safe_values("Queue", ["L2", "L3"]) == ["L2", "L3"]
    assert onboarding._safe_values("Status", [str(i) for i in range(20)]) is None  # too many values


def test_draft_try_and_save_a_mapping_override(ops_profile_rw, tmp_path):
    paths = ops_profile_rw.paths
    file = _csv(paths.inbox / "sys_user_group_real.csv", GROUP_HEADER, GROUP_ROWS)
    draft_info = onboarding.new_draft(file, paths)
    folder = Path(draft_info["draft_dir"])
    assert folder.parent == paths.runs and folder.name.startswith("map-")
    assert Path(draft_info["profile"]).is_file() and draft_info["best_candidate"]["mapping"] == "servicenow_group"
    assert draft_info["best_candidate"]["missing_required"] == ["name"]  # "Group name" is not a known alias

    base = ImportOptions(
        files=[file], dry_run=True, allow_unmanifested=True, move_files=False, mapping="servicenow_group"
    )
    failed = run_import(paths, base)
    assert failed["files"][0]["status"] == "error"

    draft = _yaml(
        Path(draft_info["out"]), {"extends": "servicenow_group", "fields": {"name": {"from+": ["Group name"]}}}
    )
    spec = onboarding.load_mapping_draft(draft, paths)
    assert (
        spec.name == "servicenow_group"
        and "Group name" in spec.fields["name"].from_
        and "name" in spec.fields["name"].from_
    )
    tried = run_import(
        paths,
        ImportOptions(
            files=[file], dry_run=True, force=True, allow_unmanifested=True, move_files=False, mapping=spec.name,
            extra_mappings={spec.name: spec},
        ),
    )  # fmt: skip
    result = tried["files"][0]
    assert result["status"] == "dry_run" and result["rows_valid"] == 3, result
    assert result["dq"]["would_soft_delete"] > 0  # a partial group file would retire the other groups
    assert all("Anna" not in json.dumps(s) for s in result["samples"])  # manager is a person field
    with pytest.raises(ValidationFailed, match="dry run"):
        run_import(paths, ImportOptions(files=[file], extra_mappings={spec.name: spec}, allow_unmanifested=True))

    preview = onboarding.save_mapping_override(paths, draft, dry_run=True)
    target = paths.config / "ops" / "mappings" / "servicenow_group.yaml"
    assert (preview["created"] and "+  name:" in preview["diff"]) or "Group name" in preview["diff"]
    assert not target.exists()
    saved = onboarding.save_mapping_override(paths, draft)
    assert target.is_file() and saved["module"] == "ops" and saved["previous_backup"] is None
    assert "Group name" in load_mapping("servicenow_group", paths).fields["name"].from_
    again = onboarding.save_mapping_override(paths, draft)
    assert again["diff"] == "" and Path(again["previous_backup"]).name == "servicenow_group.yaml.prev"


def test_invalid_drafts_are_refused(ops_profile_rw, tmp_path):
    paths = ops_profile_rw.paths
    no_pii = _yaml(
        tmp_path / "bad.yaml",
        {
            "name": "real_groups",
            "target": "assignment_group",
            "load_mode": "full_snapshot",
            "match": {"glob": ["groups*"]},
            "fields": {"name": {"from": ["Group"], "required": True}},
        },
    )
    with pytest.raises(ValidationFailed, match="Invalid mapping draft") as exc:
        onboarding.load_mapping_draft(no_pii, paths)
    assert any("pii" in d["loc"] for d in exc.value.details)
    with pytest.raises(ValidationFailed, match="unknown mapping"):
        onboarding.load_mapping_draft(_yaml(tmp_path / "x.yaml", {"extends": "nope"}), paths)
    bad_target = _yaml(
        tmp_path / "t.yaml",
        {
            "name": "real_groups",
            "target": "nope",
            "load_mode": "full_snapshot",
            "match": {"glob": ["groups*"]},
            "fields": {"name": {"from": ["Group"], "pii": "none", "required": True}},
        },
    )
    with pytest.raises(ValidationFailed, match="unknown target"):
        onboarding.load_mapping_draft(bad_target, paths)
    new = _yaml(
        tmp_path / "new.yaml",
        {**yaml.safe_load(bad_target.read_text(encoding="utf-8")), "target": "assignment_group"},
    )
    with pytest.raises(ValidationFailed, match="pass --module"):
        onboarding.save_mapping_override(paths, new)
    saved = onboarding.save_mapping_override(paths, new, module="ops")
    assert saved["created"] and (paths.config / "ops" / "mappings" / "real_groups.yaml").is_file()


def test_config_override_is_validated_and_restored_on_failure(ops_profile_rw, tmp_path):
    paths = ops_profile_rw.paths
    target = paths.config / "sap" / "scope.yaml"
    # Lists replace the defaults: fewer areas would break charm.yaml and idoc.yaml, so keep them and change categories.
    good = _yaml(tmp_path / "scope.yaml", {"categories": ["SAP", "ERP"]})
    saved = onboarding.save_config_override(paths, "sap/scope.yaml", good)
    assert saved["owner"] == "sap" and target.is_file() and saved["created"]
    before = target.read_bytes()
    bad = _yaml(tmp_path / "bad_scope.yaml", {"groups": [{"name": "SAP-FI-L3", "area": "missing_area"}]})
    with pytest.raises(ValidationFailed, match="config is invalid"):
        onboarding.save_config_override(paths, "sap/scope.yaml", bad)
    assert target.read_bytes() == before  # restored

    settings = _yaml(tmp_path / "settings.yaml", {"reporting_tz": "Not/AZone"})
    with pytest.raises(ValidationFailed):
        onboarding.save_config_override(paths, "settings.yaml", settings)
    assert not (paths.config / "settings.yaml").exists()  # removed again

    for rel in ("../secret/pii_salt.txt", "sap/../../x.yaml", "secret/x.yaml", "ops/mappings/servicenow_group.yaml"):
        with pytest.raises(ValidationFailed):
            onboarding.save_config_override(paths, rel, good, dry_run=True)


def test_cli_try_and_reconcile(ops_profile_rw, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sed import db, metrics
    from sed.calendar import parse_period
    from sed.cli import app
    from sed.settings import load_settings

    paths = ops_profile_rw.paths
    monkeypatch.setenv("SED_DATA_ROOT", str(paths.data_dir.parent))
    runner = CliRunner()
    file = _csv(tmp_path / "sys_user_group_x.csv", GROUP_HEADER, GROUP_ROWS)
    draft = _yaml(
        tmp_path / "draft.yaml", {"extends": "servicenow_group", "fields": {"name": {"from+": ["Group name"]}}}
    )
    out = runner.invoke(app, ["mappings", "try", str(file), "--from", str(draft), "--profile", paths.profile, "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["files"][0]["rows_valid"] == 3

    settings = load_settings(paths)
    period = parse_period("2026-08", settings.reporting_tz, settings.fiscal_year_start)
    conn = db.connect(paths.db, readonly=True)
    try:
        f = metrics.Filters(kind="incident")
        volume = metrics.volume_trend(conn, f, [period])[0]
        backlog = metrics.backlog(conn, f, period.end_utc, exclude_stale=False)["total"]
        sla = metrics.sla(conn, f, period)["pct"]
    finally:
        conn.close()
    args = ["metrics", "reconcile", "--period", "2026-08", "--profile", paths.profile, "--json"]
    exact = runner.invoke(
        app,
        [*args, "--opened", str(volume["opened"]), "--resolved", str(volume["resolved"]), "--backlog", str(backlog),
         "--sla-pct", str(sla), "--min-app-link-pct", "0"],
    )  # fmt: skip
    assert exact.exit_code == 0, exact.output
    body = json.loads(exact.output)
    assert body["passed"] and {r["metric"] for r in body["rows"] if r["ok"]} == {
        "incidents_opened", "incidents_resolved", "backlog_at_period_end", "sla_pct", "incidents_linked_to_app_pct"
    }  # fmt: skip
    link = next(r for r in body["rows"] if r["metric"] == "incidents_linked_to_app_pct")
    assert 0 < link["sed"] <= 100
    strict = runner.invoke(app, [*args, "--min-app-link-pct", "100.01"])
    assert not json.loads(strict.output)["passed"]  # the link gate alone can fail the reconciliation
    off = runner.invoke(app, [*args, "--opened", str(int(volume["opened"] * 1.1) + 5), "--sla-pct", str(sla + 3)])
    rows = {r["metric"]: r for r in json.loads(off.output)["rows"]}
    assert not json.loads(off.output)["passed"]
    assert (
        rows["incidents_opened"]["ok"] is False and rows["sla_pct"]["ok"] is False and rows["p1_opened"]["ok"] is None
    )
