"""`sed data move`: move the data root (every profile in it, the machine agent config and the Claude Code records) to
another folder, for example out of an app's private storage (docs/data-location.md).

Nothing is deleted. The copy is verified before the old root changes: each profile database is copied with the SQLite
backup API while this command holds its write lock, then its schema, meta and row counts are compared, and each salt
is compared with the original. Only then is the old root marked as moved, which makes sed refuse to use it, and
.claude/settings.local.json is pointed at the new folders. Profiles created with --data-dir elsewhere stay where they
are.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from sed import claude_setup, db
from sed.errors import PreconditionFailed, SedError, ValidationFailed
from sed.paths import (
    MOVED_MARKER,
    app_package_location,
    data_root,
    enclosing_git_tree,
    is_unc,
    is_under_onedrive,
    moved_to,
)
from sed.salt import fingerprint, read_salt
from sed.settings import load_agent_config

SKIPPED_NAMES = ("serve.lock",)
SKIPPED_SUFFIXES = (".db-wal", ".db-shm", ".db-journal")  # the backup API copies committed WAL content


def move_data_root(to: Path, *, dry_run: bool = False) -> dict[str, Any]:
    source = Path(os.path.abspath(data_root()))
    target = Path(os.path.abspath(to))
    _check_move(source, target)
    databases, audit_logs, files, folders = _inventory(source)
    result: dict[str, Any] = {
        "from": str(source),
        "physical_from": str(app_package_location(source) or source),
        "to": str(target),
        "dry_run": dry_run,
        "profiles": [rel.parts[0] for rel in databases],
        "files": len(databases) + len(audit_logs) + len(files),
        "bytes": sum((source / rel).stat().st_size for rel in (*databases, *audit_logs, *files)),
        "verified": [],
        "audit_logs": [],
        "warnings": [],
    }
    if dry_run:
        return result

    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    marked = False
    try:
        packaged = app_package_location(target)  # a new folder under %LOCALAPPDATA% is only redirected once it exists
        if packaged is not None:
            raise PreconditionFailed(f"{target} is redirected into an app's private storage ({packaged}).")
        with contextlib.ExitStack() as locks:
            for rel in (*databases, *audit_logs):
                conn = locks.enter_context(contextlib.closing(db.connect(source / rel)))
                locks.enter_context(db.write_tx(conn))  # no other writer commits until the old root is marked
            for rel in folders:
                (target / rel).mkdir(parents=True, exist_ok=True)
            for rel in files:
                shutil.copy2(source / rel, target / rel)
                if (target / rel).stat().st_size != (source / rel).stat().st_size:
                    raise PreconditionFailed(f"Copying {source / rel} was incomplete.")
            result["verified"] = [_copy_database(source, target, rel, result["warnings"]) for rel in databases]
            result["audit_logs"] = [_copy_audit_log(source, target, rel) for rel in audit_logs]
            claude_setup.rebase_registry(source, target)
            marker = {"moved_to": str(target), "moved_at": db.utc_now()}
            (source / MOVED_MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
            marked = True
    except BaseException:
        if not marked:
            _discard(target, created)
        raise

    try:
        result["claude"] = claude_setup.write_settings_local(load_agent_config(), root=target)
    except SedError as exc:
        result["warnings"].append(
            f"Claude Code settings were not updated ({exc.message}). Fix that, then run `sed init --profile <profile>` "
            "once SED_DATA_ROOT is set."
        )
    result["next_steps"] = [
        f"Set SED_DATA_ROOT={target} for your Windows account: Start > 'Edit environment variables for your account'.",
        "Restart the programs that run sed (the Claude app, open terminals) so they see the variable.",
        "Check with `uv run sed doctor --profile <profile>`.",
        f"The old folder is kept and sed refuses to use it; delete it when you are happy: {result['physical_from']}",
    ]
    return result


def _check_move(source: Path, target: Path) -> None:
    already = moved_to(source)
    if already is not None:
        raise PreconditionFailed(f"{source} was already moved to {already}; set SED_DATA_ROOT to that folder.")
    if not source.is_dir():
        raise PreconditionFailed(f"There is no data root to move at {source}.")
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValidationFailed(f"--to must be outside the current data root {source} and must not contain it.")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise PreconditionFailed(f"{target} must be a new or empty folder.")
    problems = []
    if is_unc(target):
        problems.append("it is a network path")
    if is_under_onedrive(target):
        problems.append("OneDrive syncs it")
    git_tree = enclosing_git_tree(target)
    if git_tree is not None:
        problems.append(f"it is inside the git working tree {git_tree}")
    packaged = app_package_location(target)
    if packaged is not None:
        problems.append(f"it is redirected into an app's private storage ({packaged})")
    if problems:
        raise PreconditionFailed(f"{target} cannot hold SED data: {'; '.join(problems)}.")
    for profile_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        holder = db.serve_lock_holder(profile_dir / "serve.lock")
        if holder is not None:
            raise PreconditionFailed(
                f"`sed serve` is running for profile '{profile_dir.name}' (pid {holder}); stop it before moving data."
            )


def _is_audit_log(rel: Path) -> bool:
    return len(rel.parts) == 3 and rel.parts[1] == "audit" and rel.name == "audit.db"


def _inventory(source: Path) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """(profile databases, audit trails, other files, folders) under the data root, as paths relative to it.

    Both kinds of database are copied with SQLite's backup, which carries what still sits in the -wal file (skipped
    as a plain file): for an audit trail those are its newest entries."""
    databases: list[Path] = []
    audit_logs: list[Path] = []
    files: list[Path] = []
    folders: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(source):
        here = Path(dirpath).relative_to(source)
        folders += [here / name for name in sorted(dirnames)]
        for name in sorted(filenames):
            rel = here / name
            if name in SKIPPED_NAMES or name.endswith(SKIPPED_SUFFIXES):
                continue
            if name == "sed.db" and len(rel.parts) == 2:
                databases.append(rel)
            elif _is_audit_log(rel):
                audit_logs.append(rel)
            else:
                files.append(rel)
    return databases, audit_logs, files, folders


def _copy_audit_log(source: Path, target: Path, rel: Path) -> dict[str, Any]:
    """Copy one audit trail and check the copy holds the same unbroken chain."""
    from sed.audit.store import verify

    src = sqlite3.connect(f"file:{(source / rel).resolve().as_posix()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(target / rel))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()
    before, after = verify(source / rel), verify(target / rel)
    keys = ("entries", "intact", "last_at")
    if [after[k] for k in keys] != [before[k] for k in keys]:
        raise PreconditionFailed(f"The copy of the audit trail {source / rel} does not match the original.")
    return {"profile": rel.parts[0], "entries": after["entries"]}


def _copy_database(source: Path, target: Path, rel: Path, warnings: list[str]) -> dict[str, Any]:
    src_path, dest_path = source / rel, target / rel
    src = db.connect(src_path, readonly=True)
    dest = db.connect(dest_path)
    try:
        src.backup(dest)
        db.enable_wal(dest)
        check = dest.execute("PRAGMA quick_check").fetchone()[0]
        before, after = db.info(src, src_path), db.info(dest, dest_path)
    finally:
        src.close()
        dest.close()
    differs = [key for key in ("user_version", "meta", "row_counts") if before[key] != after[key]]
    if check != "ok" or differs:
        raise PreconditionFailed(
            f"The copy of {src_path} does not match the original (quick_check {check}, {differs})."
        )

    profile = rel.parts[0]
    salt_rel = Path(profile, "secret", "pii_salt.txt")
    salt = read_salt(target / salt_rel)
    if salt != read_salt(source / salt_rel):
        raise PreconditionFailed(f"The salt of profile '{profile}' did not copy correctly.")
    expected = after["meta"].get("salt_fingerprint")
    if expected and salt is None:
        warnings.append(f"Profile '{profile}' had no salt file before the move; restore it from your backup.")
    elif expected and fingerprint(salt) != expected:
        warnings.append(f"The salt of profile '{profile}' already did not match its database before the move.")
    return {"profile": profile, "tables": len(after["row_counts"]), "rows": sum(after["row_counts"].values())}


def _discard(target: Path, created: bool) -> None:
    """Remove a failed copy. The folder was new or empty when the move started, so everything in it came from us."""
    if created:
        shutil.rmtree(target, ignore_errors=True)
        return
    for child in target.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
