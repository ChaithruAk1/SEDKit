"""App factory (M7 D3): `sed modules new` scaffolds a module with every surface wired and registered, refuses taken or
invalid keys, and `sed modules gate` passes the built-in modules and names what a broken module lacks."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sed import modules
from sed.errors import PreconditionFailed, ValidationFailed
from sed.modules import gate as G
from sed.modules import scaffold as S
from tests.conftest import REPO

REGISTERED = (
    "src/sed/modules/__init__.py",
    "config/modules.yaml",
    "web/src/api/fixtures/index.ts",
    "web/src/api/fixtures/core.ts",
    "docs/modules.md",
)


def _repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in REGISTERED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, root / rel)
    return root


def _cli(*args: str) -> tuple[int, dict]:
    from sed.cli import app

    result = CliRunner().invoke(app, [*args, "--json"])
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    return result.exit_code, json.loads(lines[-1])


@pytest.mark.parametrize("key", ["delivery", "sap", "ops"])
def test_gate_passes_the_builtin_modules(key):
    report = G.gate(REPO, key)
    assert report["passed"], [c for c in report["checks"] if not c["ok"]]
    assert [c["name"] for c in report["checks"]] == [
        "manifest",
        "boundaries",
        "owned_paths",
        "config",
        "skills",
        "web",
        "tests",
        "docs",
    ]


def test_scaffold_plan_registers_every_surface(tmp_path):
    root = _repo_copy(tmp_path)
    files = S.plan(
        root,
        "crm",
        title="Customer relations",
        description="Accounts and service requests",
        depends_on=["ops"],
        order=95,
    )
    assert {
        f"src/sed/modules/crm/{n}" for n in ("__init__.py", "api.py", "api_models.py", "cli.py", "CLAUDE.md")
    } <= set(files)
    assert "web/src/modules/crm/pages/CrmOverviewPage.tsx" in files and "tests/modules/crm/test_crm_module.py" in files
    assert '"sed.modules.delivery", "sed.modules.crm")' in files["src/sed/modules/__init__.py"]
    assert "enabled: [ops, sap, delivery, crm]" in files["config/modules.yaml"]
    index = files["web/src/api/fixtures/index.ts"]
    assert "import * as crm from './crm';" in index and "'/api/crm/overview': () => crm.overview()," in index
    assert "id: 'crm.overview'" in files["web/src/api/fixtures/core.ts"]
    assert "| `crm` | Accounts and service requests" in files["docs/modules.md"]
    manifest = files["src/sed/modules/crm/__init__.py"]
    assert 'depends_on=("ops",)' in manifest and "__" not in manifest.replace("__init__", "").replace("__future__", "")
    for text in files.values():
        assert "__KEY__" not in text and "__CLS__" not in text and "__TITLE__" not in text
    compile(manifest, "crm/__init__.py", "exec")
    compile(files["src/sed/modules/crm/api.py"], "crm/api.py", "exec")

    written = S.write(root, files)
    assert (root / "src/sed/modules/crm/api.py").is_file() and len(written) == len(files)
    with pytest.raises(PreconditionFailed):
        S.plan(root, "crm")  # the package now exists


@pytest.mark.parametrize(
    ("key", "error"),
    [("Bad", ValidationFailed), ("x", ValidationFailed), ("ops", PreconditionFailed), ("review", PreconditionFailed)],
)
def test_scaffold_refuses_invalid_or_taken_keys(tmp_path, key, error):
    with pytest.raises(error):
        S.plan(_repo_copy(tmp_path), key)


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    """A repo copy with the scaffolded `crm` module, importable as an extra module for the test only."""
    import sed.modules as registry

    root = _repo_copy(tmp_path)
    S.write(root, S.plan(root, "crm", title="Customer relations"))

    def forget() -> None:
        for name in [n for n in sys.modules if n == "sed.modules.crm" or n.startswith("sed.modules.crm.")]:
            del sys.modules[name]

    forget()
    monkeypatch.setattr(registry, "__path__", [*registry.__path__, str(root / "src" / "sed" / "modules")])
    monkeypatch.setattr(registry, "_cache", {})
    monkeypatch.setenv(modules.EXTRA_ENV, "sed.modules.crm")
    yield root
    forget()


def test_scaffolded_module_imports_and_passes_the_manifest_gate(scaffolded):
    root = scaffolded
    module = modules.get("crm")
    assert module.title == "Customer relations"
    report = G.gate(root, "crm")
    checks = {c["name"]: c for c in report["checks"]}
    assert checks["manifest"]["ok"] and checks["boundaries"]["ok"] and checks["owned_paths"]["ok"], checks
    assert checks["tests"]["ok"] and checks["docs"]["ok"], checks
    assert checks["web"]["problems"] == ["contracts/openapi.json is missing (run scripts/codegen.py)"]


def test_gate_names_missing_parts(scaffolded):
    root = scaffolded
    (root / "src/sed/modules/crm/CLAUDE.md").unlink()
    shutil.rmtree(root / "tests/modules/crm")
    bad = root / "src/sed/modules/crm/extra.py"
    bad.write_text("from sed.modules.sap import scope\n", encoding="utf-8")
    report = G.gate(root, "crm")
    problems = {c["name"]: c["problems"] for c in report["checks"]}
    assert not report["passed"]
    assert any("without depending on 'sap'" in p for p in problems["boundaries"])
    assert problems["tests"] and problems["docs"]


def test_cli_new_dry_run_and_gate(tmp_path):
    code, body = _cli(
        "modules", "new", "crm", "--title", "Customer relations", "--root", str(_repo_copy(tmp_path)), "--dry-run"
    )
    assert code == 0 and body["dry_run"] and "src/sed/modules/crm/__init__.py" in body["files"]
    assert not (tmp_path / "repo/src/sed/modules/crm").exists()
    code, body = _cli("modules", "gate", "delivery")
    assert code == 0 and body["passed"]
    code, body = _cli("modules", "gate", "nope")
    assert code == 2
