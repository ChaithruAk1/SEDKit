"""`sed modules gate <key>`: the quality gate a module passes before it is committed.

Static checks, fast and offline (no database, no network):
* `manifest`: the module's declarations are valid and every import reference loads;
* `boundaries`: core code never imports the module, and the module imports other modules only through `depends_on`;
* `owned_paths`: declared, and covering the module's package, its config, tests and web folders that exist, and its
  skill folders and workflows;
* `config`: declared config files, report specs and the mappings folder exist under `config/<key>/`;
* `skills`: every declared skill has `.claude/skills/<name>/SKILL.md` whose frontmatter name matches;
* `web`: nav items have a web module whose routes cover their paths, and every GET route under `/api/<key>/` in
  `contracts/openapi.json` has a fixture handler;
* `tests`: `tests/modules/<key>/` has at least one `test_*.py`;
* `docs`: the module has a CLAUDE.md and a row in `docs/modules.md`.
`--run-tests` also runs `pytest tests/modules/<key>` and reports its exit code.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from sed.errors import ValidationFailed

CORE_DIRS = ("ai", "api", "reports", "ingest", "connectors")


def _imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", ""))
            in (
                "import_module",
                "__import__",
            )
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.append(node.args[0].value)
    return names


def _check(name: str, problems: list[str]) -> dict[str, Any]:
    return {"name": name, "ok": not problems, "problems": problems}


def _manifest(module: Any) -> list[str]:
    from sed import modules

    problems = [p for p in modules.validate() if p.startswith(f"{module.key}:") or f"'{module.key}'" in p]
    for ref in modules.declared_refs(module):
        try:
            modules.load_ref(ref)
        except ValidationFailed as exc:
            problems.append(exc.message)
    return problems


def _boundaries(root: Path, module: Any) -> list[str]:
    from sed import modules

    src = root / "src" / "sed"
    own = f"sed.modules.{module.key}"
    problems = []
    core = [p for p in src.glob("*.py")]
    for sub in CORE_DIRS:
        core += list((src / sub).rglob("*.py")) if (src / sub).is_dir() else []
    core += [src / "modules" / name for name in ("__init__.py", "contract.py", "cli.py", "scaffold.py", "gate.py")]
    for path in core:
        if path.is_file() and any(n == own or n.startswith(own + ".") for n in _imports(path)):
            problems.append(f"core file {path.relative_to(root).as_posix()} imports {own}")
    allowed = {module.key, *module.depends_on}
    others = {m.key for m in modules.installed()} - allowed
    package = src / "modules" / module.key
    for path in sorted(package.rglob("*.py")) if package.is_dir() else []:
        for name in _imports(path):
            parts = name.split(".")
            if len(parts) >= 3 and parts[:2] == ["sed", "modules"] and parts[2] in others:
                problems.append(f"{path.relative_to(root).as_posix()} imports {name} without depending on '{parts[2]}'")
    return sorted(set(problems))


def _owned_paths(root: Path, module: Any) -> list[str]:
    key = module.key
    if not module.owned_paths:
        return ["owned_paths is empty"]
    expected = [f"src/sed/modules/{key}/**"]
    for rel in (f"config/{key}", f"tests/modules/{key}", f"web/src/modules/{key}"):
        if (root / rel).is_dir():
            expected.append(f"{rel}/**")
    for skill in module.skills:
        expected.append(f".claude/skills/{skill.name}/**")
        expected += [f".claude/workflows/{workflow}" for workflow in skill.workflows]
    return [f"owned_paths does not cover {e}" for e in expected if e not in module.owned_paths]


def _config(root: Path, module: Any) -> list[str]:
    config = root / "config"
    problems = [f"config file config/{rel} is missing" for rel in module.config_files if not (config / rel).is_file()]
    problems += [f"report spec config/{r.spec} is missing" for r in module.reports if not (config / r.spec).is_file()]
    if module.mappings_dir and not (config / module.mappings_dir).is_dir():
        problems.append(f"mappings folder config/{module.mappings_dir} is missing")
    return problems


def _skills(root: Path, module: Any) -> list[str]:
    problems = []
    for skill in module.skills:
        path = root / ".claude" / "skills" / skill.name / "SKILL.md"
        if not path.is_file():
            problems.append(f"skill {skill.name} has no {path.relative_to(root).as_posix()}")
            continue
        match = re.search(r"^name:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        if not match or match.group(1) != skill.name:
            problems.append(f"skill {skill.name}: SKILL.md frontmatter name must be '{skill.name}'")
    return problems


def _route_regex(path: str) -> re.Pattern[str]:
    parts = [("[^/]+" if part.startswith(":") else re.escape(part)) for part in path.strip("/").split("/")]
    return re.compile("^" + "/".join(parts) + "$")


def _web(root: Path, module: Any) -> list[str]:
    key = module.key
    problems = []
    web = root / "web" / "src" / "modules" / key
    if module.nav:
        index = web / "index.ts"
        if not index.is_file():
            problems.append(f"nav items need web/src/modules/{key}/index.ts")
        routes_text = "".join(p.read_text(encoding="utf-8") for p in sorted(web.glob("routes.ts*")))
        routes = [_route_regex(p) for p in re.findall(r"path:\s*'([^']+)'", routes_text)]
        for item in module.nav:
            if not any(r.match(item.path.strip("/")) for r in routes):
                problems.append(f"nav item {item.id} ({item.path}) has no web route")
    openapi = root / "contracts" / "openapi.json"
    fixtures = root / "web" / "src" / "api" / "fixtures" / "index.ts"
    if module.api and not openapi.is_file():
        problems.append("contracts/openapi.json is missing (run scripts/codegen.py)")
    elif module.api:
        spec = json.loads(openapi.read_text(encoding="utf-8"))
        gets = sorted(p for p, ops in spec.get("paths", {}).items() if p.startswith(f"/api/{key}/") and "get" in ops)
        if not gets:
            problems.append(f"no GET route under /api/{key}/ in contracts/openapi.json (run scripts/codegen.py)")
        handlers = fixtures.read_text(encoding="utf-8") if fixtures.is_file() else ""
        missing = [p for p in gets if f"'{p}'" not in handlers]
        problems += [f"GET {p} has no fixture handler in web/src/api/fixtures/index.ts" for p in missing]
    return problems


def _tests(root: Path, module: Any) -> list[str]:
    folder = root / "tests" / "modules" / module.key
    if not any(folder.glob("test_*.py")):
        return [f"tests/modules/{module.key}/ has no test_*.py"]
    return []


def _docs(root: Path, module: Any) -> list[str]:
    problems = []
    if not (root / "src" / "sed" / "modules" / module.key / "CLAUDE.md").is_file():
        problems.append(f"src/sed/modules/{module.key}/CLAUDE.md is missing")
    docs = root / "docs" / "modules.md"
    if not docs.is_file() or f"| `{module.key}` |" not in docs.read_text(encoding="utf-8"):
        problems.append(f"docs/modules.md has no row for `{module.key}`")
    return problems


def gate(root: Path, key: str, *, run_tests: bool = False) -> dict[str, Any]:
    from sed import modules

    module = modules.get(key)
    checks = [
        _check("manifest", _manifest(module)),
        _check("boundaries", _boundaries(root, module)),
        _check("owned_paths", _owned_paths(root, module)),
        _check("config", _config(root, module)),
        _check("skills", _skills(root, module)),
        _check("web", _web(root, module)),
        _check("tests", _tests(root, module)),
        _check("docs", _docs(root, module)),
    ]
    if run_tests:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", f"tests/modules/{key}", "-q", "-p", "no:cacheprovider"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-1:]
        checks.append(_check("pytest", [] if proc.returncode == 0 else [f"pytest exit {proc.returncode}", *tail]))
    return {"module": key, "passed": all(c["ok"] for c in checks), "checks": checks}
