"""`sed doctor`: environment and profile health checks. Any `fail` makes the command exit 4.

Doctor never crashes on a broken environment: bad config, a corrupt database or a malformed salt are reported
as failing checks so every other check still runs.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from sed import claude_setup, db
from sed.errors import SedError
from sed.paths import (
    Paths,
    app_package_location,
    data_root,
    enclosing_git_tree,
    is_unc,
    is_under_onedrive,
    repo_root,
)
from sed.salt import fingerprint, read_salt
from sed.settings import PII_MODES, load_agent_config, load_settings


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str


def _check(name: str, ok: bool, detail: str, *, severity: str = "fail") -> Check:
    return Check(name, "ok" if ok else severity, detail)


def run_checks(paths: Paths, *, skip: set[str] | None = None) -> list[Check]:
    skip = skip or set()
    checks: list[Check] = []

    def add(check: Check) -> None:
        if check.name not in skip:
            checks.append(check)

    ddir = paths.data_dir
    add(_check("python_version", sys.version_info[:2] in {(3, 11), (3, 12)}, sys.version.split()[0]))
    add(Check("sqlite_version", "ok", sqlite3.sqlite_version))

    add(_check("data_dir_not_unc", not is_unc(ddir), str(ddir)))
    add(_check("data_dir_not_onedrive", not is_under_onedrive(ddir), "DATA_DIR must not be OneDrive-synced"))
    packaged = app_package_location(ddir)
    add(
        _check(
            "data_dir_not_in_app_storage",
            packaged is None,
            f"really stored in an app's private storage at {packaged}: programs outside that app cannot see it and "
            "removing the app deletes it. Move it with `sed data move --to <folder>` (docs/data-location.md)"
            if packaged
            else "not inside an app's private storage",
            severity="fail" if paths.data_class == "real" else "warn",
        )
    )
    git_tree = enclosing_git_tree(ddir) if ddir.exists() else None
    add(_check("data_dir_not_in_git_tree", git_tree is None, f"inside git tree {git_tree}" if git_tree else "ok"))

    meta = _database_checks(paths, add)
    _settings_checks(paths, add)
    _salt_checks(paths, meta, add)

    add(
        _check(
            "env_uv_no_sync",
            os.environ.get("UV_NO_SYNC") == "1",
            "UV_NO_SYNC=1 (set in .claude/settings.json env)",
            severity="warn",
        )
    )
    add(_check("env_pythonutf8", os.environ.get("PYTHONUTF8") == "1", "PYTHONUTF8=1", severity="warn"))

    root = repo_root()
    hook = _git_hooks_dir(root) / "pre-commit"
    hook_ok = hook.is_file() and "pre-commit" in hook.read_text(encoding="utf-8", errors="ignore")
    add(_check("git_hooks_installed", hook_ok, str(hook) if hook_ok else "run `uv run pre-commit install`"))
    email = _git_local_email(root)
    add(
        _check(
            "git_local_user_email",
            bool(email),
            email or "set `git config --local user.email <work-appropriate address>` before the first commit",
            severity="warn",
        )
    )
    denylist = data_root() / "guard" / "denylist.txt"
    add(
        _check(
            "guard_denylist_present",
            denylist.is_file(),
            str(denylist) if denylist.is_file() else f"no denylist at {denylist}: real-name leak check is off",
            severity="fail" if paths.data_class == "real" else "warn",
        )
    )

    _claude_checks(paths, add)

    clashes, bad_names = skill_name_problems(root)
    add(_check("skill_names_valid", not bad_names, ", ".join(bad_names) or "all sed-* with matching frontmatter name"))
    add(_check("skill_names_no_personal_clash", not clashes, ", ".join(clashes) or "no clashes with ~/.claude/skills"))
    _module_checks(paths, root, add)
    _connector_checks(paths, add)

    if paths.data_class == "real":
        add(
            _check(
                "ai_approval_recorded",
                meta.get("ai_real_data_approved") == "true",
                "record with `sed init --profile real --ai-approval-note ...`",
                severity="warn",
            )
        )
    return checks


def _database_checks(paths: Paths, add) -> dict[str, str]:
    db_exists = paths.db.is_file()
    add(_check("db_exists", db_exists, str(paths.db) if db_exists else "run `sed init`"))
    if not db_exists:
        return {}
    meta: dict[str, str] = {}
    try:
        conn = db.connect(paths.db, readonly=True)
        try:
            version = db.user_version(conn)
            latest = db.latest_version()
            add(_check("schema_current", version == latest, f"user_version={version}, latest={latest}"))
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            add(_check("wal_mode", str(mode).lower() == "wal", f"journal_mode={mode}"))
            fts = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'ticket_fts'").fetchone()[0]
            add(_check("fts5_available", fts == 1, "ticket_fts virtual table present" if fts else "missing"))
            meta = db.all_meta(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        add(Check("db_readable", "fail", f"{paths.db}: {exc}"))
        return {}
    add(
        _check(
            "data_class_matches_profile",
            meta.get("data_class") == paths.data_class,
            f"profile={paths.profile}, meta.data_class={meta.get('data_class')}",
        )
    )
    pii = meta.get("pii_mode")
    pii_ok = pii in PII_MODES and not (pii == "keep" and paths.data_class == "real")
    add(_check("pii_mode_valid", pii_ok, f"pii_mode={pii}"))
    return meta


def _settings_checks(paths: Paths, add) -> None:
    try:
        settings = load_settings(paths)
    except SedError as exc:
        add(Check("settings_valid", "fail", f"{exc.message}: {exc.details}"))
        return
    add(Check("settings_valid", "ok", "settings.yaml (repo + DATA_DIR override)"))
    try:
        ZoneInfo(settings.reporting_tz)
        add(Check("tzdata", "ok", f"reporting_tz={settings.reporting_tz}"))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        add(Check("tzdata", "fail", f"timezone '{settings.reporting_tz}' unusable: {exc}"))


def _module_checks(paths: Paths, root: Path, add) -> None:
    from sed import modules

    try:
        problems = modules.validate()
        for m in modules.enabled(paths):
            for ref in modules.declared_refs(m):
                try:
                    modules.load_ref(ref)
                except SedError as exc:
                    problems.append(f"{m.key}: {exc.message}")
        modules.mapping_index(paths)
        modules.ingest_targets(paths)
        overlaps = modules.mapping_glob_overlaps(paths)
    except SedError as exc:
        add(Check("modules_valid", "fail", f"{exc.message}: {exc.details}" if exc.details else exc.message))
        return
    unknown = sorted(modules.enabled_keys(paths) - {m.key for m in modules.installed()})
    warnings = [f"config/modules.yaml lists unknown module(s): {', '.join(unknown)}"] if unknown else []
    if overlaps:
        warnings.append("mapping globs overlap across modules: " + "; ".join(overlaps))
    if problems:
        add(Check("modules_valid", "fail", "; ".join(problems)))
    elif warnings:
        add(Check("modules_valid", "warn", "; ".join(warnings)))
    else:
        add(Check("modules_valid", "ok", "enabled: " + ", ".join(m.key for m in modules.enabled(paths))))

    skills_dir = root / ".claude" / "skills"
    folders = sorted(p.name for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
    declared = [s.name for m in modules.installed() for s in m.skills] + list(modules.CORE_SKILLS)
    unclaimed = [f for f in folders if f.startswith("sed-") and declared.count(f) != 1]
    add(
        _check(
            "skills_claimed",
            not unclaimed,
            ", ".join(unclaimed) or "every sed-* skill belongs to one module or the core",
        )
    )
    for check in modules.doctor_checks(paths):
        add(check)


def _connector_checks(paths: Paths, add) -> None:
    """connectors.yaml is valid; enabled connectors have their secret; their watermarks are recent (no network)."""
    from datetime import UTC, datetime

    from sed.connectors.pull import status

    try:
        report = status(paths)
    except SedError as exc:
        add(Check("connectors_valid", "fail", f"{exc.message}: {exc.details}" if exc.details else exc.message))
        return
    enabled = [c for c in report["connectors"] if c["enabled"]]
    add(Check("connectors_valid", "ok", "enabled: " + (", ".join(c["connector"] for c in enabled) or "none")))
    if not enabled:
        return
    missing = [f"{c['connector']} (secret '{c['credential']}')" for c in enabled if not c["credential_found_in"]]
    add(
        _check(
            "connector_credentials",
            not missing,
            "missing: " + ", ".join(missing) if missing else "every enabled connector has its secret",
            severity="fail" if paths.data_class == "real" else "warn",
        )
    )
    now = datetime.now(UTC)
    stale = []
    for c in enabled:
        for s in c["sources"]:
            mark = s["watermark"]
            if mark is None or (now - datetime.fromisoformat(mark)).days > 14:
                stale.append(f"{c['connector']}/{s['source']} ({mark or 'never pulled'})")
    add(
        _check(
            "connector_watermarks_fresh",
            not stale,
            "older than 14 days: " + ", ".join(stale) if stale else "every source pulled within 14 days",
            severity="warn",
        )
    )


def _salt_checks(paths: Paths, meta: dict[str, str], add) -> None:
    try:
        salt = read_salt(paths.salt_file)
    except SedError as exc:
        add(Check("salt_present", "fail", exc.message))
        return
    if salt is None:
        hint = "restore the original salt file" if meta.get("salt_fingerprint") else "run `sed init --new-salt`"
        add(Check("salt_present", "fail", f"missing at {paths.salt_file}: {hint}"))
        return
    add(Check("salt_present", "ok", str(paths.salt_file)))
    if meta.get("salt_fingerprint"):
        add(_check("salt_fingerprint_matches", fingerprint(salt) == meta["salt_fingerprint"], "salt vs meta"))


def _claude_checks(paths: Paths, add) -> None:
    try:
        agent = load_agent_config()
    except SedError as exc:
        add(Check("agent_config_valid", "fail", f"{exc.message}: {exc.details}"))
        return
    local = claude_setup.settings_local_path()
    runs_ok = False
    try:
        data = json.loads(local.read_bytes().decode("utf-8-sig")) if local.is_file() else {}
        runs_ok = str(paths.runs) in data.get("permissions", {}).get("additionalDirectories", [])
    except (UnicodeDecodeError, json.JSONDecodeError):
        add(Check("claude_settings_local", "fail", f"{local} is not valid JSON"))
    else:
        add(_check("claude_settings_local", runs_ok, f"{local} lists {paths.runs}" if runs_ok else "re-run `sed init`"))
    add(
        _check(
            "claude_md_prefix_in_sync",
            claude_setup.prefix_block_in_sync(agent),
            "CLAUDE.md prefix block",
            severity="warn",
        )
    )
    try:
        rule_ok = claude_setup.prefix_allow_rule_present(agent)
    except SedError:
        rule_ok = False
    add(_check("prefix_allow_rule", rule_ok, f"allow rule for '{agent.command_prefix}'"))
    surface_problems = project_surface_problems(repo_root())
    add(
        _check(
            "claude_project_surfaces",
            not surface_problems,
            ", ".join(surface_problems) or "skills, commands, agents and the PreToolUse guard hook",
        )
    )


def _git_hooks_dir(root: Path) -> Path:
    """The hooks folder git actually uses for `root`.

    `.git/hooks` is right only in a plain checkout. In a worktree (`.claude/worktrees/`, docs/playbooks/
    parallel-build.md) `.git` is a file pointing at the main repository, and git runs the hooks the worktrees share;
    `core.hooksPath` can move them again. `git rev-parse --git-path hooks` answers for all three, so ask git rather
    than assume, and fall back to the plain spelling when git is not on PATH.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return root / ".git" / "hooks"
    answer = out.stdout.strip()
    if out.returncode != 0 or not answer:
        return root / ".git" / "hooks"
    path = Path(answer)
    return path if path.is_absolute() else root / path


def _git_local_email(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--local", "--get", "user.email"], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return None
    return out.stdout.strip() or None


def project_surface_problems(root: Path) -> list[str]:
    """The project's Claude Code surfaces: skills, commands and agents present, and the guard hook wired and there."""
    surfaces = ("skills", "commands", "agents", "hooks")
    problems = [f"no .claude/{kind}" for kind in surfaces if not (root / ".claude" / kind).is_dir()]
    settings = root / ".claude" / "settings.json"
    try:
        data = json.loads(settings.read_bytes().decode("utf-8-sig")) if settings.is_file() else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [*problems, f"{settings} is not valid JSON"]
    entries = data.get("hooks", {}).get("PreToolUse", []) if isinstance(data, dict) else []
    commands = [h.get("command", "") for entry in entries for h in entry.get("hooks", [])]
    scripts = [name for command in commands for name in re.findall(r"\.claude/hooks/[\w.-]+", command)]
    if not scripts:
        problems.append("no PreToolUse hook in .claude/settings.json")
    problems += [f"missing hook script {name}" for name in scripts if not (root / name).is_file()]
    return problems


def skill_name_problems(root: Path) -> tuple[list[str], list[str]]:
    """(clashes with personal skills, folders that are not sed-* or whose frontmatter name differs)."""
    skills_dir = root / ".claude" / "skills"
    personal = Path.home() / ".claude" / "skills"
    clashes, bad = [], []
    if not skills_dir.is_dir():
        return clashes, bad
    for folder in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        if not folder.name.startswith("sed-"):
            bad.append(f"{folder.name} (not sed- prefixed)")
        skill_md = folder / "SKILL.md"
        name = None
        if skill_md.is_file():
            parts = skill_md.read_text(encoding="utf-8").split("---")
            if len(parts) >= 3:
                try:
                    name = (yaml.safe_load(parts[1]) or {}).get("name")
                except yaml.YAMLError:
                    name = None
        if name != folder.name:
            bad.append(f"{folder.name} (frontmatter name={name})")
        if (personal / folder.name).exists():
            clashes.append(folder.name)
    return clashes, bad


def summarize(checks: list[Check]) -> dict[str, Any]:
    counts = {s: sum(1 for c in checks if c.status == s) for s in ("ok", "warn", "fail")}
    return {
        "status": "fail" if counts["fail"] else ("warn" if counts["warn"] else "ok"),
        "counts": counts,
        "checks": [asdict(c) for c in checks],
    }
