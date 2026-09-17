"""PreToolUse hook: refuse tool calls that reach into the data folder or hand-edit generated files.

CLAUDE.md states the contract ("Never open sed.db, inbox\\, secret\\, config\\ or ground_truth\\ under DATA_DIR",
"Generated contracts live in contracts/ ... never hand-edit"). Claude Code deny rules cover Read and Edit but not a
shell command, so this hook closes that gap for Bash, PowerShell, Grep and Glob as well.

Wired in .claude/settings.json under hooks.PreToolUse, which resolves this file through CLAUDE_PROJECT_DIR rather
than a path relative to the working directory: Python exits 2 when it cannot open a file, and 2 is what Claude Code
reads as "block this call", so a hook that loses its own script would refuse every tool in the session.
It reads the hook event as JSON on stdin and exits 2 with a reason on stderr to block the call; anything unexpected
exits 0, because a bug here must never stop a session.
Real enforcement still lives in the pre-commit guard and scripts/guard_confidential.py: this is defence in depth.
"""

from __future__ import annotations

import json
import os
import re
import sys

PROTECTED = ("inbox", "secret", "config", "ground_truth")
PATH_FIELDS = ("file_path", "notebook_path", "path", "command", "pattern", "glob")
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
DRIVE_PREFIX = re.compile(r"(?<![a-z0-9])/{1,2}([a-z])/")


def normalise(text: str) -> str:
    """Lower case, forward slashes, and Git Bash drive spellings (//c/x, /c/x) folded to c:/x."""
    return DRIVE_PREFIX.sub(r"\1:/", text.replace("\\", "/").lower())


def data_roots() -> list[str]:
    """Every folder a SED data root could be on this machine, normalised."""
    roots = [os.environ.get("SED_DATA_ROOT") or ""]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(os.path.join(local_app_data, "sed"))
    return [normalise(root.rstrip("\\/")) for root in roots if root]


def _strings(tool_input: dict) -> list[str]:
    return [value for key in PATH_FIELDS if isinstance(value := tool_input.get(key), str)]


def decide(event: dict) -> str | None:
    """The reason to block this tool call, or None to let it through."""
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for raw in _strings(tool_input):
        text = normalise(raw)
        for root in data_roots():
            root_re = re.escape(root)
            folders = "|".join(PROTECTED)
            if re.search(rf"{root_re}/[^/\s\"']+/({folders})(?![a-z0-9_])", text):
                return (
                    f"{tool} touches a protected folder under the data root ({', '.join(PROTECTED)}). "
                    "Use the CLI instead: `uv run sed <command> --profile <p> --json`. See the hard rules in CLAUDE.md."
                )
            if re.search(rf"{root_re}/guard(?![a-z0-9_])", text):
                return f"{tool} touches the guard denylist, which holds real names. Never read or copy it."
        if re.search(r"/sed\.db(?![a-z0-9_])", text):
            return (
                f"{tool} opens sed.db. Only SED's Python code writes or reads the database; "
                "agents use `uv run sed ...` and packets under runs/."
            )
        if tool in EDIT_TOOLS and re.search(r"(^|/)(contracts/|[^/]*output_schema\.json$)", text):
            return (
                f"{tool} edits a generated file. Change its Pydantic source instead and run "
                "`uv run python scripts/codegen.py`."
            )
    return None


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        reason = decide(event) if isinstance(event, dict) else None
    except (OSError, ValueError, re.error):  # a broken hook must never block a session
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
