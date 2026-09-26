"""The actions SED puts on its audit trail, with their plain-English wording, shared by the dashboard and the command
line so both describe the same action the same way.

Each `start_*` writes the attempt before the action (and so refuses it when the trail is unavailable) and returns the
`Attempt`; the matching `*_done` records the outcome from the action's result. Use the attempt as a context manager
around the action: an exception records the failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sed.audit.record import Attempt, start
from sed.auth.actor import Actor
from sed.paths import Paths

CONNECTOR_LABELS = {
    "servicenow": "ServiceNow",
    "jira": "Jira",
    "confluence": "Confluence",
    "sharepoint": "SharePoint",
    "sap": "SAP",
}


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _import_detail(result: dict[str, Any] | None) -> dict[str, Any]:
    """Per file: its name, status and row counts, and for a refused file only the kind of error. Never an error's
    message or details, which can quote the file's own rows (the trail is kept forever)."""
    if not result:
        return {}
    files = []
    for f in result.get("files") or []:
        row = {k: f.get(k) for k in ("status", "rows_read", "rows_rejected") if f.get(k) is not None}
        row["file"] = Path(str(f.get("file") or "")).name
        error = f.get("error")
        if isinstance(error, dict) and error.get("kind"):
            row["error"] = str(error["kind"])
        files.append(row)
    return {"summary": result.get("summary"), "files": files}


def _import_words(result: dict[str, Any] | None) -> str:
    summary = (result or {}).get("summary") or {}
    files, rows = int(summary.get("imported", 0)), int(summary.get("rows_read", 0))
    words = f"{_plural(files, 'file')} imported, {_plural(rows, 'row')} read"
    errors = int(summary.get("errors", 0))
    return words + (f", {_plural(errors, 'file')} refused" if errors else "")


# -- imports -------------------------------------------------------------------------------------------------------


def start_import(paths: Paths, actor: Actor, files: list[Path] | None, *, inbox: bool = False) -> Attempt:
    names = [p.name for p in files or []]
    what = "Import the files waiting in the inbox" if inbox or not names else "Import " + ", ".join(names[:5])
    if len(names) > 5:
        what += f" and {len(names) - 5} more"
    return start(paths, actor, "import", summary=what, target_type="files", detail={"files": names or None})


def start_upload(paths: Paths, actor: Actor, name: str, size: int | None) -> Attempt:
    detail = {"file": name, "bytes": size}
    return start(
        paths, actor, "import", summary=f"Upload and import {name}", target_type="file", target_id=name, detail=detail
    )


def import_done(attempt: Attempt, result: dict[str, Any] | None) -> None:
    attempt.done(f"{attempt.what}: {_import_words(result)}.", detail=_import_detail(result))


# -- pulls ---------------------------------------------------------------------------------------------------------


def start_pull(
    paths: Paths, actor: Actor, connector: str, *, source: str | None, full: bool, then_import: bool
) -> Attempt:
    label = CONNECTOR_LABELS.get(connector, connector)
    what = f"Pull from {label}" + (f" ({source})" if source else "") + (" and import" if then_import else "")
    detail = {"connector": connector, "source": source, "full": full or None, "import": then_import or None}
    return start(paths, actor, "pull", summary=what, target_type="connector", target_id=connector, detail=detail)


def pull_done(attempt: Attempt, pulled: dict[str, Any], imported: dict[str, Any] | None = None) -> None:
    sources = [
        {k: s.get(k) for k in ("source", "rows", "capped", "files", "from") if s.get(k) not in (None, [], False)}
        for s in pulled.get("sources") or []
    ]
    rows = sum(int(s.get("rows") or 0) for s in pulled.get("sources") or [])
    words = f"{_plural(rows, 'row')} pulled"
    if imported is not None:
        words += f"; {_import_words(imported)}"
    detail = {
        "sources": sources,
        "pages": pulled.get("pages"),
        **({"import": _import_detail(imported)} if imported else {}),
    }
    attempt.done(f"{attempt.what}: {words}.", detail=detail)


# -- clearing and restoring ----------------------------------------------------------------------------------------


def start_clear(paths: Paths, actor: Actor, source: str) -> Attempt:
    from sed.dataclear import BY_KEY

    known = BY_KEY.get(source)
    label = getattr(known, "label", None) or source
    return start(
        paths, actor, "clear", summary=f"Clear the data imported from {label}", target_type="source", target_id=source
    )


def clear_done(attempt: Attempt, result: dict[str, Any]) -> None:
    rows = int(result.get("rows") or 0)
    detail = {k: result.get(k) for k in ("deleted", "detached", "import_batches")}
    attempt.done(f"{attempt.what}: {_plural(rows, 'row')} deleted.", detail=detail)


def start_inbox_prune(paths: Paths, actor: Actor, older_than: str, folders: list[str]) -> Attempt:
    what = f"Delete processed export files older than {older_than} from the inbox"
    detail = {"older_than": older_than, "folders": folders}
    return start(paths, actor, "clear", summary=what, target_type="inbox", detail=detail)


def inbox_prune_done(attempt: Attempt, removed: list[str]) -> None:
    attempt.done(f"{attempt.what}: {_plural(len(removed), 'folder')} deleted.", detail={"folders": removed})


def start_restore(paths: Paths, actor: Actor, backup_file: Path) -> Attempt:
    what = f"Restore the database from the backup {backup_file.name}"
    return start(paths, actor, "restore", summary=what, target_type="backup", target_id=backup_file.name)


def restore_done(attempt: Attempt, result: dict[str, Any]) -> None:
    safety = Path(str(result.get("safety_backup") or "")).name or None
    detail = {
        "safety_backup": safety,
        "schema_version": result.get("user_version"),
        "data_class": result.get("data_class"),
    }
    attempt.done(f"{attempt.what}: done; the replaced data was kept as {safety or 'no backup'}.", detail=detail)


# -- reports -------------------------------------------------------------------------------------------------------


def start_report_build(
    paths: Paths, actor: Actor, report: str, period: str, vendor: str | None, params: dict[str, Any]
) -> Attempt:
    what = f"Build the {report} report for {period}" + (f" ({vendor})" if vendor else "")
    detail = {k: v for k, v in params.items() if v not in (None, [], False)}
    if isinstance(detail.get("template_map"), str):  # a name, or a path to a template map: keep only the file name
        detail["template_map"] = Path(detail["template_map"]).name
    return start(paths, actor, "report_build", summary=what, target_type="report", target_id=report, detail=detail)


def report_build_done(attempt: Attempt, result: dict[str, Any]) -> None:
    artifacts = result.get("artifacts") or []
    files = [a.get("file_name") or Path(str(a.get("path", ""))).name for a in artifacts]
    formats = ", ".join(str(a.get("format", "")).upper() for a in artifacts)
    attempt.done(
        f"{attempt.what}: built {formats or 'no files'}.",
        detail={"files": files, "snapshot": result.get("snapshot_id")},
    )


def start_export(
    paths: Paths, actor: Actor, what: str, target_type: str, target_id: str | None, detail: dict
) -> Attempt:
    return start(paths, actor, "export", summary=what, target_type=target_type, target_id=target_id, detail=detail)


# -- AI runs -------------------------------------------------------------------------------------------------------


def start_ai_run(paths: Paths, actor: Actor, skill: str, params: dict[str, Any]) -> Attempt:
    detail = {k: v for k, v in params.items() if v not in (None, [], False) and k != "claude_version"}
    return start(
        paths,
        actor,
        "ai_run",
        summary=f"Prepare data for Claude ({skill})",
        target_type="skill",
        target_id=skill,
        detail=detail,
    )


def ai_run_done(attempt: Attempt, plan: dict[str, Any]) -> None:
    counts = plan.get("plan") or {}
    items, batches = int(counts.get("items", 0)), int(counts.get("batches", 0))
    words = f"{_plural(items, 'item')} in {_plural(batches, 'batch', 'batches')} handed to Claude"
    detail = {"run_id": plan.get("run_id"), "plan": counts}
    attempt.done(f"{attempt.what}: {words} (run {plan.get('run_id')}).", detail=detail)


def start_export_profile(paths: Paths, actor: Actor, file: Path) -> Attempt:
    """The column profile of an export (headers, fill rates, value shapes, safe categories) written for Claude."""
    what = f"Prepare the column profile of {file.name} for Claude (sed-map-export)"
    return start(paths, actor, "ai_run", summary=what, target_type="file", target_id=file.name)


def export_profile_done(attempt: Attempt, draft: dict[str, Any]) -> None:
    folder = Path(str(draft.get("draft_dir") or "")).name
    attempt.done(f"{attempt.what}: written to {folder}.", detail={"draft": folder})


# -- moving the data folder ----------------------------------------------------------------------------------------


def start_data_move(paths: Paths, actor: Actor, target: Path) -> Attempt:
    what = f"Move the data folder to {target}"
    return start(paths, actor, "data_move", summary=what, target_type="folder", target_id=target.name)


# -- sign-in settings ----------------------------------------------------------------------------------------------


def settings_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Every setting whose value differs, with its value before and after."""
    return [
        {"field": key, "before": _get(before, key), "after": _get(after, key)}
        for key in sorted(set(_flatten(before)) | set(_flatten(after)))
        if _get(before, key) != _get(after, key)
    ]


def start_sign_in_settings(
    paths: Paths, actor: Actor, what: str, before: dict[str, Any], after: dict[str, Any]
) -> Attempt:
    """A change to who may sign in, on record with its values before and after before the file is changed."""
    changes = settings_changes(before, after)
    return start(
        paths, actor, "sign_in_settings", summary=what, target_type="settings", target_id="auth.yaml", changes=changes
    )


def _flatten(data: dict[str, Any], prefix: str = "") -> list[str]:
    keys: list[str] = []
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        keys += _flatten(value, dotted + ".") if isinstance(value, dict) else [dotted]
    return keys


def _get(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value
