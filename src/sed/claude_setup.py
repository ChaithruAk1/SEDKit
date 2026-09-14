"""Machine-local Claude Code wiring written by `sed init`.

* .claude/settings.local.json (gitignored): additionalDirectories + POSIX-form ``//c/...`` Read/Edit allow rules
  for each profile's runs\\ folder, deny rules for ground_truth/secret/inbox/config, and Bash/PowerShell allow rules
  for the effective command prefix when it differs from the committed default.
* CLAUDE.md: the command-prefix block between markers is rendered from the machine-level agent config.

Ownership: sed records exactly which entries it wrote in <data_root>\\claude_managed.json and only ever removes
those, so user-added rules (including deny rules that mention sed) are preserved.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from sed.errors import PreconditionFailed
from sed.paths import Paths, data_root, repo_root
from sed.settings import AgentConfig

PREFIX_START = "<!-- sed:prefix:start -->"
PREFIX_END = "<!-- sed:prefix:end -->"
DEFAULT_ALLOW_PREFIX = "uv run sed"
PERMISSION_KEYS = ("additionalDirectories", "allow", "deny")
BLOCKED_SUBDIRS = ("ground_truth", "secret", "inbox", "config")


def posix_rule_path(path: Path) -> str:
    """C:\\Users\\me\\AppData\\Local\\sed\\synthetic -> //c/Users/me/AppData/Local/sed/synthetic"""
    resolved = Path(os.path.abspath(path))
    drive = resolved.drive
    rest = resolved.as_posix()[len(drive) :] if drive else resolved.as_posix()
    if drive and drive.endswith(":"):
        return f"//{drive[0].lower()}{rest}"
    return "/" + rest.lstrip("/")


def settings_local_path() -> Path:
    env = os.environ.get("SED_CLAUDE_SETTINGS_LOCAL")
    return Path(env) if env else repo_root() / ".claude" / "settings.local.json"


def claude_md_path() -> Path:
    env = os.environ.get("SED_CLAUDE_MD")
    return Path(env) if env else repo_root() / "CLAUDE.md"


def _registry_file(root: Path | None = None) -> Path:
    return (root or data_root()) / "profiles.json"


def _managed_file(root: Path | None = None) -> Path:
    return (root or data_root()) / "claude_managed.json"


def _read_json(path: Path, *, what: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreconditionFailed(f"Cannot parse {what} at {path}: {exc}. Fix or remove it, then re-run.") from exc
    if not isinstance(data, dict):
        raise PreconditionFailed(f"{what} at {path} must be a JSON object.")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def register_profile(paths: Paths) -> None:
    """Remember profile data dirs (including custom --data-dir locations) for settings regeneration."""
    registry = _read_json(_registry_file(), what="sed profile registry")
    dirs = registry.setdefault("data_dirs", {})
    dirs[str(Path(os.path.abspath(paths.data_dir)))] = paths.profile
    _write_json(_registry_file(), registry)


def initialized_profiles(root: Path | None = None) -> list[tuple[str, Path]]:
    """Profiles with a database: directories directly under the data root plus registered custom dirs."""
    base = root or data_root()
    found: dict[str, tuple[str, Path]] = {}
    if base.is_dir():
        for p in base.iterdir():
            if p.is_dir() and (p / "sed.db").is_file():
                found[str(Path(os.path.abspath(p)))] = (p.name, p)
    registry = _read_json(_registry_file(base), what="sed profile registry")
    for d, name in registry.get("data_dirs", {}).items():
        p = Path(d)
        if (p / "sed.db").is_file():
            found[str(Path(os.path.abspath(p)))] = (name, p)
    return sorted(found.values(), key=lambda t: (t[0], str(t[1])))


def rebase_registry(old_root: Path, new_root: Path) -> None:
    """After a data-root move, point registered profile dirs that were inside the old root at the new root."""
    registry = _read_json(_registry_file(new_root), what="sed profile registry")
    if not registry.get("data_dirs"):
        return
    rebased = {}
    for d, name in registry["data_dirs"].items():
        p = Path(d)
        rebased[str(new_root / p.relative_to(old_root)) if p.is_relative_to(old_root) else d] = name
    registry["data_dirs"] = rebased
    _write_json(_registry_file(new_root), registry)


def managed_rules(profiles: list[tuple[str, Path]], agent: AgentConfig) -> dict[str, list[str]]:
    additional: list[str] = []
    allow: list[str] = []
    deny: list[str] = []
    for _, pdir in profiles:
        runs = pdir / "runs"
        additional.append(str(runs))
        rp = posix_rule_path(runs)
        allow += [f"Read({rp}/**)", f"Edit({rp}/**)"]
        for blocked in BLOCKED_SUBDIRS:
            bp = posix_rule_path(pdir / blocked)
            deny += [f"Read({bp}/**)", f"Edit({bp}/**)"]
    prefix = agent.command_prefix.strip()
    if prefix != DEFAULT_ALLOW_PREFIX:
        allow += [f"Bash({prefix}:*)", f"PowerShell({prefix}:*)"]
    return {"additionalDirectories": additional, "allow": allow, "deny": deny}


def write_settings_local(agent: AgentConfig, target: Path | None = None, root: Path | None = None) -> dict[str, Any]:
    """Regenerate sed-owned entries in settings.local.json; never touch entries sed did not write."""
    path = target or settings_local_path()
    data = _read_json(path, what="Claude settings.local.json")
    perms = data.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise PreconditionFailed(f"'permissions' in {path} must be a JSON object.")
    previous = _read_json(_managed_file(root), what="sed managed-entries record")
    rules = managed_rules(initialized_profiles(root), agent)
    for key in PERMISSION_KEYS:
        existing = perms.get(key, [])
        if not isinstance(existing, list):
            raise PreconditionFailed(f"'permissions.{key}' in {path} must be a list.")
        prev_owned = set(previous.get(key, []))
        kept = [e for e in existing if e not in prev_owned and e not in rules[key]]
        perms[key] = kept + rules[key]
    _write_json(path, data)
    _write_json(_managed_file(root), rules)
    return {"path": str(path), **rules}


def render_prefix_block(agent: AgentConfig) -> str:
    return (
        f"{PREFIX_START}\n"
        f"- Command prefix: `{agent.command_prefix}` (always through the {agent.shell_tool} tool)\n"
        f"- Fallback if uv is unavailable: `{agent.fallback_prefix}`\n"
        f"{PREFIX_END}"
    )


_BLOCK_RE = re.compile(re.escape(PREFIX_START) + r".*?" + re.escape(PREFIX_END), re.DOTALL)


def render_claude_md(agent: AgentConfig, claude_md: Path | None = None) -> bool:
    """Rewrite the prefix block in CLAUDE.md. Returns True if the file changed."""
    path = claude_md or claude_md_path()
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if not _BLOCK_RE.search(text):
        return False
    new = _BLOCK_RE.sub(lambda _: render_prefix_block(agent), text)
    if new != text:
        path.write_text(new, encoding="utf-8", newline="\n")
        return True
    return False


def prefix_block_in_sync(agent: AgentConfig, claude_md: Path | None = None) -> bool:
    path = claude_md or claude_md_path()
    return path.is_file() and render_prefix_block(agent) in path.read_text(encoding="utf-8")


def prefix_allow_rule_present(agent: AgentConfig, target: Path | None = None) -> bool:
    prefix = agent.command_prefix.strip()
    if prefix == DEFAULT_ALLOW_PREFIX:
        return True
    data = _read_json(target or settings_local_path(), what="Claude settings.local.json")
    return f"Bash({prefix}:*)" in data.get("permissions", {}).get("allow", [])
