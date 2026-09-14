"""CLI output: machine-readable JSON (for agents) or Rich text (for humans)."""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable
from typing import Any

from rich.console import Console

_console: Console | None = None


def console() -> Console:
    global _console
    if _console is None:
        _console = Console(highlight=False, soft_wrap=True)
    return _console


def ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def emit(
    payload: dict[str, Any],
    as_json: bool,
    human: Callable[[dict[str, Any]], None] | None = None,
    *,
    ok: bool = True,
) -> None:
    if as_json:
        sys.stdout.write(json.dumps({"ok": ok, **payload}, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
        return
    if human is not None:
        human(payload)
    else:
        console().print_json(json.dumps(payload, ensure_ascii=False, default=str))


def emit_error(error: dict[str, Any], as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps({"ok": False, "error": error}, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
        return
    err = Console(stderr=True, highlight=False, soft_wrap=True)
    err.print(f"[bold red]{error.get('kind', 'error')}:[/] {error.get('message')}")
    if error.get("details") is not None:
        err.print_json(json.dumps(error["details"], ensure_ascii=False, default=str))
