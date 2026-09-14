"""Ops module health checks, appended to `sed doctor` as ops.*."""

from __future__ import annotations

from typing import Any

from sed.errors import SedError
from sed.paths import Paths

LEGACY_FILES = ("taxonomy.yaml", "sla.yaml", "risk_rules.yaml", "vendor_groups.yaml", "ci_to_app.yaml")


def _config_problems(paths: Paths) -> list[str]:
    from sed.modules import get
    from sed.reports.specs import load_report_spec
    from sed.settings import load_layered

    problems: list[str] = []
    try:
        categories = load_layered("ops/taxonomy.yaml", paths).get("categories") or {}
        if not categories:
            problems.append("taxonomy has no categories")
        problems += [
            f"category '{c}' has no description" for c, v in categories.items() if not (v or {}).get("description")
        ]
        for name in ("ops/sla.yaml", "ops/risk_rules.yaml", "ops/vendor_groups.yaml"):
            load_layered(name, paths)
        for rdef in get("ops").reports:
            load_report_spec(rdef.key, paths)
    except SedError as exc:
        problems.append(f"{exc.message}: {exc.details}" if exc.details else exc.message)
    return problems


def _legacy_locations(paths: Paths) -> list[str]:
    legacy = []
    for folder, target in (("mappings", "ops/mappings"), ("reports", "ops/reports")):
        if (paths.config / folder).is_dir() and any((paths.config / folder).glob("*.yaml")):
            legacy.append(f"config/{folder}/*.yaml -> config/{target}/")
    legacy += [f"config/{f} -> config/ops/{f}" for f in LEGACY_FILES if (paths.config / f).is_file()]
    return legacy


def checks(paths: Paths) -> list[Any]:
    from sed.doctor import Check

    problems = _config_problems(paths)
    legacy = _legacy_locations(paths)
    return [
        Check("ops.config_valid", "fail" if problems else "ok", "; ".join(problems) or "ops config and specs load"),
        Check(
            "ops.legacy_config_paths",
            "warn" if legacy else "ok",
            "move DATA_DIR overrides: " + ", ".join(legacy) if legacy else "no legacy DATA_DIR config locations",
        ),
    ]
