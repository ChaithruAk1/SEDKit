"""Single CI entry point (local and corporate CI): `uv run python scripts/ci.py`.

Offline, no Claude calls. Steps that do not apply yet (no workflows, no web app) are reported as skipped.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Strict-schema digest baselines (validated by tests/platform/test_baseline_files.py) are excluded from detect-secrets.
BASELINE_FILE = re.compile(r"^tests/fixtures/synthetic/[^/]+_baseline\.json$")
PY = sys.executable
SCRIPTS_DIR = Path(PY).parent


def tool(name: str) -> str:
    for candidate in (SCRIPTS_DIR / f"{name}.exe", SCRIPTS_DIR / name):
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(name)
    return found


def tracked_text_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
        files = [f for f in out.splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = []
    exclude = {"uv.lock", "web/package-lock.json", ".secrets.baseline"}
    return [f for f in files if f not in exclude and not BASELINE_FILE.match(f) and (REPO / f).is_file()]


def steps() -> list[tuple[str, list[str] | None, str]]:
    out: list[tuple[str, list[str] | None, str]] = [
        ("ruff check", [PY, "-m", "ruff", "check", "."], ""),
        ("ruff format", [PY, "-m", "ruff", "format", "--check", "."], ""),
        ("pytest", [PY, "-m", "pytest", "-m", "not slow"], ""),
        ("guard (tracked files)", [PY, "scripts/guard_confidential.py", "--all"], ""),
    ]
    files = tracked_text_files()
    if files:
        out.append(("detect-secrets", [tool("detect-secrets-hook"), "--baseline", ".secrets.baseline", *files], ""))
    else:
        out.append(("detect-secrets", None, "no tracked files yet (not a git repo or nothing committed)"))

    workflows = sorted((REPO / ".claude" / "workflows").glob("*.js"))
    harness = REPO / "tests" / "workflow_harness.mjs"
    if workflows and harness.exists() and shutil.which("node"):
        out.append(("workflow scripts", ["node", str(harness), "--check", *map(str, workflows)], ""))
    else:
        out.append(("workflow scripts", None, "no workflows yet"))

    web = REPO / "web" / "package.json"
    npm = shutil.which("npm")
    if web.exists() and npm:
        out.append(("web typecheck", [npm, "--prefix", "web", "run", "typecheck"], ""))
        out.append(("web build", [npm, "--prefix", "web", "run", "build"], ""))
    else:
        out.append(("web", None, "no web app yet"))
    return out


def main() -> int:
    env = {**os.environ, "PYTHONUTF8": "1", "UV_NO_SYNC": "1"}
    results = []
    for name, cmd, skip_reason in steps():
        if cmd is None:
            print(f"--- {name}: skipped ({skip_reason})")
            results.append((name, "skipped", 0.0))
            continue
        print(f"--- {name}", flush=True)
        start = time.perf_counter()
        proc = subprocess.run(cmd, cwd=REPO, env=env)
        elapsed = time.perf_counter() - start
        results.append((name, "ok" if proc.returncode == 0 else f"FAILED ({proc.returncode})", elapsed))
    print("\n=== CI summary ===")
    for name, status, elapsed in results:
        print(f"{status:>14}  {name:<24} {elapsed:6.1f}s")
    return 1 if any(s.startswith("FAILED") for _, s, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
