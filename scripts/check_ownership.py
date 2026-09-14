"""Check that a workstream branch only changed files it owns (docs/m2/ownership.yaml) and left no stubs behind.

    python scripts/check_ownership.py ws1-ai [--base main]   # commits unique to HEAD plus uncommitted changes
    python scripts/check_ownership.py --no-stubs               # no NotImplementedByWorkstream left anywhere

Only commits reachable from HEAD but not from the base count (merge commits excluded), so integrator `contract:`
commits pulled in by a rebase are ignored. Exit 0 when clean, 1 with a JSON list of violations.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
STUB_ALLOWED = {"src/sed/errors.py", "src/sed/api/errors.py"}


def glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def matches(path: str, patterns: list[str]) -> bool:
    return any(glob_regex(p).match(path) for p in patterns)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def changed_paths(repo: Path, base: str) -> list[str]:
    # --no-renames: a committed rename lists both its source and its destination, so moving a frozen or other-owned
    # file into an owned path is still a violation.
    committed = _git(
        repo, "log", "--no-merges", "--no-renames", "--name-only", "--format=", f"{base}..HEAD"
    ).splitlines()
    paths = {p.strip() for p in committed if p.strip()}
    for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines():
        entry = line[3:]
        for part in entry.split(" -> "):
            paths.add(part.strip().strip('"'))
    return sorted(p.replace("\\", "/") for p in paths if p)


def stub_pattern(ws: str) -> re.Pattern[str]:
    return re.compile(r"NotImplementedByWorkstream\(\s*(?:['\"]" + re.escape(ws) + r"['\"]|WS\s*\))")


def check_workstream(repo: Path, config: dict, ws: str, base: str) -> list[dict[str, str]]:
    if ws not in config["workstreams"]:
        return [{"path": "", "reason": f"unknown workstream '{ws}'"}]
    spec = config["workstreams"][ws]
    owns, may_regen = spec.get("owns") or [], spec.get("may_regenerate") or []
    violations = []
    for path in changed_paths(repo, base):
        if matches(path, config.get("generated") or []):
            if not matches(path, may_regen):
                violations.append(
                    {"path": path, "reason": "generated file (regenerate only if listed in may_regenerate)"}
                )
            continue
        if matches(path, config.get("frozen") or []):
            violations.append({"path": path, "reason": "frozen file (request a contract change)"})
        elif not matches(path, owns):
            violations.append({"path": path, "reason": f"not owned by {ws}"})
    ws_constant = re.compile(r"^WS\s*=\s*['\"]" + re.escape(ws) + r"['\"]", re.MULTILINE)
    for path in sorted({p for p in _git(repo, "ls-files").splitlines() if matches(p, owns)}):
        file = repo / path
        if not file.is_file() or file.suffix not in {".py", ".ts", ".tsx"}:
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        literal = re.search(r"NotImplementedByWorkstream\(\s*['\"]" + re.escape(ws) + r"['\"]", text)
        via_constant = ws_constant.search(text) and re.search(r"NotImplementedByWorkstream\(\s*WS\s*\)", text)
        if literal or via_constant:
            violations.append({"path": path, "reason": f"stub still raises NotImplementedByWorkstream for {ws}"})
    return violations


def check_no_stubs(repo: Path) -> list[dict[str, str]]:
    out = []
    for path in _git(repo, "ls-files", "src").splitlines():
        if path in STUB_ALLOWED or not path.endswith(".py"):
            continue
        if "NotImplementedByWorkstream(" in (repo / path).read_text(encoding="utf-8", errors="replace"):
            out.append({"path": path, "reason": "NotImplementedByWorkstream stub left in source"})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workstream", nargs="?")
    parser.add_argument("--base", default="main")
    parser.add_argument("--no-stubs", action="store_true")
    parser.add_argument("--repo", default=str(REPO))
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    config = yaml.safe_load((repo / "docs" / "m2" / "ownership.yaml").read_text(encoding="utf-8"))
    if args.no_stubs:
        violations = check_no_stubs(repo)
    elif args.workstream:
        violations = check_workstream(repo, config, args.workstream, args.base)
    else:
        parser.error("give a workstream key or --no-stubs")
    print(json.dumps({"ok": not violations, "violations": violations}, indent=1))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
