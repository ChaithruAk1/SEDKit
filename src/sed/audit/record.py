"""What every action calls to put itself on the audit trail (docs/audit.md).

* `record(paths, actor, action, summary=...)`: one entry, for something that happens at once (a sign-in, a download).
* `start(paths, actor, action, summary=...)`: the attempt, written *before* the action; the returned `Attempt` writes
  the outcome after it (`done` or `failed`, and as a context manager `failed` on any exception). An action sent out
  is thus on record even if SED stops half-way; an attempt without an outcome reads "no outcome recorded".

Both raise `AuditUnavailable` when the entry cannot be written, and callers call them before acting, so an action the
trail cannot record does not happen. Outcomes are written best effort: the action has already happened by then.

A command-line action is recorded under `sed.auth.actor.command_line_actor()`: the Windows account, never proven.
`record` adds a note of whoever was signed in to the dashboard at the time (`dashboard-session.json`, written by the
dashboard).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sed import __version__
from sed.auth.actor import Actor, Method
from sed.errors import PreconditionFailed, SedError

if TYPE_CHECKING:
    from sed.auth.runtime import AuthEvent
    from sed.paths import Paths

log = logging.getLogger("sed.audit")

# Every action the trail knows, with the words the Audit page uses for it.
ACTIONS = {
    "sign_in": "Sign-in",
    "sign_out": "Sign-out",
    "serve_start": "SED started",
    "serve_stop": "SED stopped",
    "download": "Download",
    "report_build": "Report built",
    "export": "Export",
    "pull": "Pull",
    "import": "Import",
    "clear": "Data cleared",
    "restore": "Backup restored",
    "ai_run": "AI run",
    "sign_in_settings": "Sign-in settings changed",
    "data_move": "Data folder moved",
}
OUTCOMES = ("started", "done", "failed", "refused")
LABELS = {"microsoft": "Microsoft", "google": "Google", "github": "GitHub"}
SESSION_FILE = "dashboard-session.json"


class AuditUnavailable(PreconditionFailed):
    """The audit trail could not take an entry, so the action it describes must not happen."""


def audit_dir(paths: Paths) -> Path:
    return paths.audit


def audit_path(paths: Paths) -> Path:
    return paths.audit / "audit.db"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if value else None


def _append(paths: Paths, actor: Actor, action: str, outcome: str, summary: str, **fields: Any) -> str:
    if action not in ACTIONS or outcome not in OUTCOMES:
        raise ValueError(f"unknown audit action or outcome: {action}/{outcome}")
    detail = dict(fields.get("detail") or {})
    if actor.channel == "command_line":
        detail.setdefault("dashboard_signed_in", dashboard_signed_in(paths))
    row = {
        "entry_id": uuid.uuid4().hex,
        "at": utc_now(),
        "actor": actor.id,
        "actor_name": actor.name,
        "method": actor.method,
        "verified": 1 if actor.verified else 0,
        "channel": actor.channel,
        "action": action,
        "outcome": outcome,
        "target_type": fields.get("target_type"),
        "target_id": None if fields.get("target_id") is None else str(fields["target_id"]),
        "summary": summary.strip()[:500],
        "detail": _json(detail),
        "changes": _json(fields.get("changes")),
        "correlation_id": fields.get("correlation_id"),
        "profile": paths.profile,
        "sed_version": __version__,
    }
    from sed.audit.store import append

    try:
        return str(append(audit_path(paths), row, moved=lambda: _moved_trail(paths))["entry_id"])
    except (OSError, ValueError, SedError, sqlite3.Error) as exc:
        raise AuditUnavailable(
            f"SED could not record this in its audit trail ({type(exc).__name__}), so it did not do it. "
            "Try again; if it keeps happening, run `sed doctor`."
        ) from exc


def _moved_trail(paths: Paths) -> Path | None:
    """Where this profile's trail lives now, when `sed data move` moved the data folder after `paths` was resolved (a
    command still running during the move records its outcome in the new place, not in the abandoned one)."""
    from sed.paths import moved_to

    new_root = moved_to(paths.data_dir.parent)
    return None if new_root is None else Path(new_root) / paths.profile / "audit" / "audit.db"


def record(
    paths: Paths,
    actor: Actor,
    action: str,
    *,
    summary: str,
    outcome: str = "done",
    target_type: str | None = None,
    target_id: object | None = None,
    detail: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
) -> str:
    """One entry. Raises AuditUnavailable when it cannot be written: call it before handing anything out."""
    return _append(
        paths,
        actor,
        action,
        outcome,
        summary,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        changes=changes,
    )


def file_sha256(path: Path) -> str:
    """The fingerprint of a file handed out, so the trail can later say exactly which file it was."""
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


FAILURE_WORDS = {
    "validation": "the input was refused",
    "precondition": "a condition was not met",
    "busy": "the database was busy",
    "not_implemented": "not available yet",
}


def failure_reason(exc: BaseException) -> str:
    """Why an action failed, in fixed words. Never the error's own text: it can carry file contents or paths, and the
    trail keeps everything forever. SED's own errors give their kind, anything else its type."""
    if isinstance(exc, SedError):
        return FAILURE_WORDS.get(exc.kind, exc.kind)
    return f"unexpected {type(exc).__name__}"


@dataclass
class Attempt:
    """An action on record as started; `done` or `failed` records how it ended (once)."""

    paths: Paths
    actor: Actor
    action: str
    what: str  # the summary of the attempt, e.g. "Pull from ServiceNow"
    target_type: str | None
    target_id: object | None
    correlation_id: str
    closed: bool = field(default=False)

    def done(
        self, summary: str | None = None, *, detail: dict[str, Any] | None = None, changes: list | None = None
    ) -> None:
        self._close("done", summary or self.what, detail, changes)

    def failed(self, reason: str, *, detail: dict[str, Any] | None = None) -> None:
        self._close("failed", f"{self.what}: failed ({reason})", detail, None)

    def _close(self, outcome: str, summary: str, detail: dict[str, Any] | None, changes: list | None) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            _append(
                self.paths,
                self.actor,
                self.action,
                outcome,
                summary,
                target_type=self.target_type,
                target_id=self.target_id,
                detail=detail,
                changes=changes,
                correlation_id=self.correlation_id,
            )
        except AuditUnavailable as exc:  # the action already happened; its start is on record
            log.error("audit outcome not recorded for %s: %s", self.action, exc.__cause__)

    def __enter__(self) -> Attempt:
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if exc is not None:
            self.failed(failure_reason(exc))
        elif not self.closed:
            self.done()
        return False


def start(
    paths: Paths,
    actor: Actor,
    action: str,
    *,
    summary: str,
    target_type: str | None = None,
    target_id: object | None = None,
    detail: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
) -> Attempt:
    """Record that an action is starting (raises AuditUnavailable, so it never starts unrecorded). A change carries
    each field's value before and after in `changes`, on record before anything is changed."""
    correlation = uuid.uuid4().hex
    _append(
        paths,
        actor,
        action,
        "started",
        summary,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        changes=changes,
        correlation_id=correlation,
    )
    return Attempt(paths, actor, action, summary, target_type, target_id, correlation)


# -- who is acting on the command line ------------------------------------------------------------------------------


def dashboard_signed_in(paths: Paths) -> str | None:
    """Who is signed in to this profile's dashboard right now, if a live `sed serve` says so."""
    from sed import db

    file = audit_dir(paths) / SESSION_FILE
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("expires_at", 0) <= time.time():
        return None
    pid = data.get("pid")
    if not isinstance(pid, int) or db.serve_lock_holder(paths.serve_lock) != pid:
        return None
    if data.get("started") not in (None, db.process_start_token(pid)):
        return None  # written by an earlier `sed serve` whose process number was handed out again
    actor = data.get("actor")
    return actor if isinstance(actor, str) else None


def write_dashboard_session(paths: Paths, actor: Actor, expires_at: float) -> None:
    """Note who is signed in, for command-line entries. A convenience, never a control: a failure is logged only."""
    from sed import db

    pid = os.getpid()
    payload = {
        "actor": actor.id,
        "name": actor.name,
        "method": actor.method,
        "expires_at": expires_at,
        "pid": pid,
        "started": db.process_start_token(pid),
    }
    target = audit_dir(paths) / SESSION_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{SESSION_FILE}.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        log.warning("dashboard session note not written: %s", type(exc).__name__)


def clear_dashboard_session(paths: Paths) -> None:
    try:
        (audit_dir(paths) / SESSION_FILE).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("dashboard session note not removed: %s", type(exc).__name__)


# -- sign-in events (the dashboard's AuthRuntime.on_event) ----------------------------------------------------------


def auth_recorder(paths: Paths) -> Callable[[AuthEvent], None]:
    """Put every sign-in, refusal, failure, sign-out and replaced session on the trail. A sign-in whose entry cannot be
    written raises, and the runtime then does not complete it."""

    def on_event(event: AuthEvent) -> None:
        provider = event.provider or "unknown"
        label = LABELS.get(provider, provider)
        identity = event.identity
        detail: dict[str, Any] = {"provider": provider, **{k: v for k, v in event.detail.items() if k != "expires_at"}}
        if event.kind == "sign_in" and identity is not None:
            actor = identity.actor()
            expires = float(cast(float, event.detail.get("expires_at", 0.0)))
            record(
                paths,
                actor,
                "sign_in",
                summary=f"Signed in with {label}.",
                detail={**detail, "reason": event.message, "expires_at": _iso(expires) if expires else None},
            )
            write_dashboard_session(paths, actor, expires)
            return
        if event.kind in ("sign_out", "session_replaced") and identity is not None:
            summary = "Signed out." if event.kind == "sign_out" else event.message
            record(paths, identity.actor(), "sign_out", summary=summary, detail=detail)
            if event.kind == "sign_out":
                clear_dashboard_session(paths)
            return
        # A refusal names who the provider vouched for; a failure knows nobody.
        method = cast(Method, provider) if provider in LABELS else "windows"
        if identity is not None:
            who = identity.shown()
            actor = Actor(id=who, name=identity.name or who, method=method, verified=True, channel="dashboard")
        else:
            actor = Actor(id="unknown", name="unknown", method=method, verified=False, channel="dashboard")
        outcome = "refused" if event.kind == "sign_in_refused" else "failed"
        record(
            paths,
            actor,
            "sign_in",
            outcome=outcome,
            summary=f"Sign-in with {label} {outcome}: {event.message}",
            detail=detail,
        )

    return on_event
