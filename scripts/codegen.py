"""Generate contract artefacts from code. Never hand-edit the outputs; on a merge conflict regenerate and commit.

    uv run python scripts/codegen.py            # write everything
    uv run python scripts/codegen.py --check    # write nothing; exit 1 listing drifted files
    uv run python scripts/codegen.py --only cli,openapi

Parts:
* cli     -> contracts/cli.json      (Typer command tree: commands, options, arguments)
* openapi -> contracts/openapi.json  (FastAPI app with the built-in modules)          [when sed.api exists]
* skills  -> .claude/skills/*/output_schema.json + workflow schema blocks              [when sed.ai.codegen exists]
* web     -> web/src/api/schema.d.ts via `npm --prefix web run gen:api`                [when web/node_modules exists]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CONTRACTS = REPO / "contracts"
PARTS = ("cli", "openapi", "skills", "web")


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write_or_check(path: Path, text: str, check: bool, drifted: list[str]) -> None:
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current == text:
        return
    if check:
        drifted.append(str(path.relative_to(REPO)).replace("\\", "/"))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _param(p: Any) -> dict[str, Any]:
    default = p.default
    if callable(default) or (default is not None and not isinstance(default, str | int | float | bool | list | tuple)):
        default = repr(default)
    return {
        "name": p.name,
        "kind": "option" if p.param_type_name == "option" else "argument",
        "opts": sorted(getattr(p, "opts", []) or []),
        "type": getattr(p.type, "name", str(p.type)),
        "required": bool(p.required),
        "multiple": bool(getattr(p, "multiple", False)),
        "is_flag": bool(getattr(p, "is_flag", False)),
        "default": list(default) if isinstance(default, tuple) else default,
    }


def _command_tree(cmd: Any, name: str) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "help": (cmd.help or "").strip().splitlines()[0] if cmd.help else ""}
    commands = getattr(cmd, "commands", None)
    if commands is not None:
        out["commands"] = [_command_tree(sub, sub_name) for sub_name, sub in sorted(commands.items())]
    else:
        out["params"] = [_param(p) for p in cmd.params if p.name not in {"help"}]
    return out


def cli_contract() -> dict[str, Any]:
    os.environ.pop("SED_EXTRA_MODULES", None)
    import typer.main

    from sed.cli import app

    return _command_tree(typer.main.get_command(app), "sed")


def openapi_contract() -> dict[str, Any] | None:
    if not _has_module("sed.api.app"):
        return None
    os.environ.pop("SED_EXTRA_MODULES", None)
    from sed.api.app import create_app
    from sed.modules import installed
    from sed.paths import Paths

    with tempfile.TemporaryDirectory(prefix="sed-codegen-") as tmp:
        app = create_app(Paths("synthetic", Path(tmp)), token="codegen", modules=installed(include_extra=False))
        return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--only", default=",".join(PARTS))
    args = parser.parse_args(argv)
    parts = {p.strip() for p in args.only.split(",") if p.strip()}
    unknown = parts - set(PARTS)
    if unknown:
        parser.error(f"unknown parts: {sorted(unknown)}")
    sys.path.insert(0, str(REPO / "src"))
    drifted: list[str] = []

    if "cli" in parts:
        _write_or_check(CONTRACTS / "cli.json", _dump(cli_contract()), args.check, drifted)
    if "openapi" in parts:
        spec = openapi_contract()
        if spec is not None:
            _write_or_check(CONTRACTS / "openapi.json", _dump(spec), args.check, drifted)
    if "skills" in parts and _has_module("sed.ai.codegen"):
        from sed.ai.codegen import export_all

        drifted += export_all(REPO, check=args.check)
    if "skills" in parts and _has_module("sed.ingest.onboarding"):
        from sed.ingest.onboarding import canonical_fields_markdown

        reference = REPO / ".claude" / "skills" / "sed-map-export" / "reference" / "canonical_fields.md"
        _write_or_check(reference, canonical_fields_markdown(), args.check, drifted)
    if "web" in parts and (REPO / "web" / "node_modules").is_dir() and shutil.which("npm"):
        script = "gen:api:check" if args.check else "gen:api"
        proc = subprocess.run([shutil.which("npm") or "npm", "--prefix", "web", "run", script], cwd=REPO, check=False)
        if proc.returncode != 0:
            drifted.append("web/src/api/schema.d.ts")

    if drifted:
        print(json.dumps({"ok": False, "drifted": sorted(set(drifted))}))
        return 1
    print(json.dumps({"ok": True, "parts": sorted(parts), "check": args.check}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
