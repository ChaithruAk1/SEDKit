"""Every symbol frozen code and tests import from the ingest engine keeps existing with the same shape."""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Sequence
from pathlib import Path

import pytest

from sed import modules
from tests.conftest import REPO

ENGINE = ("sed.ingest.loader", "sed.ingest.resolve", "sed.ingest.mapping", "sed.ingest.targets")
CLASS, CALLABLE = "class", "callable"
MUST_KEEP: dict[str, dict[str, str]] = {
    "sed.ingest.loader": {
        "ImportOptions": CLASS,
        "run_import": CALLABLE,
        "reresolve": CALLABLE,
        "MANIFEST": "str",
        "file_sha256": CALLABLE,
    },
    "sed.ingest.resolve": {
        "ALIAS_KINDS": "sequence",
        "Resolver": CLASS,
        "mark_resolved": CALLABLE,
        "refresh_suggestions": CALLABLE,
        "normalize_alias": CALLABLE,
    },
    "sed.ingest.mapping": {
        "MappingSpec": CLASS,
        "load_all_mappings": CALLABLE,
        "load_mapping": CALLABLE,
        "mapping_names": CALLABLE,
        "choose_mapping": CALLABLE,
        "resolve_as_of": CALLABLE,
        "resolve_columns": CALLABLE,
        "glob_matches": CALLABLE,
        "header_match_score": CALLABLE,
        "read_for_mapping": CALLABLE,
    },
    "sed.ingest.targets": {
        "TARGETS": "dict",
        "Target": CLASS,
        "Ctx": CLASS,
        "Reject": CLASS,
        "KIND_ORDER": "dict",
        "TARGET_ORDER": "list",
    },
}
CASES = [(module, name, kind) for module, names in MUST_KEEP.items() for name, kind in names.items()]


@pytest.mark.parametrize(("module", "name", "kind"), CASES, ids=[f"{m.rsplit('.', 1)[1]}.{n}" for m, n, _ in CASES])
def test_must_keep_symbol(module: str, name: str, kind: str):
    value = getattr(importlib.import_module(module), name)
    if kind == CLASS:
        assert inspect.isclass(value)
    elif kind == CALLABLE:
        assert callable(value) and not inspect.isclass(value)
    elif kind == "sequence":
        assert isinstance(value, Sequence) and all(isinstance(v, str) for v in value)
    else:
        assert type(value).__name__ == kind


def test_signatures_and_shapes_are_unchanged():
    from sed.ingest import loader, resolve, target, targets

    assert list(inspect.signature(loader.run_import).parameters) == ["paths", "opts"]
    assert list(inspect.signature(loader.reresolve).parameters) == ["paths"]
    assert loader.MANIFEST == "_manifest.json"
    for method in ("add_alias", "flush", "resolve", "register_ids"):
        assert callable(getattr(resolve.Resolver, method)), method
    assert list(inspect.signature(resolve.Resolver.add_alias).parameters) == [
        "self",
        "kind",
        "raw",
        "target_id",
        "origin",
    ]
    assert list(inspect.signature(resolve.Resolver.flush).parameters) == ["self", "batch_id"]
    assert (targets.Target, targets.Ctx, targets.Reject) == (target.Target, target.Ctx, target.Reject)
    assert issubclass(targets.Reject, Exception)
    assert all(isinstance(t, target.Target) and t.name == key for key, t in targets.TARGETS.items())
    assert set(resolve.ALIAS_KINDS) == set(modules.alias_kinds()) and "group" in resolve.ALIAS_KINDS
    assert list(resolve.ALIAS_KINDS) == list(modules.alias_kinds())
    assert ", ".join(resolve.ALIAS_KINDS).startswith("app, vendor")


def _engine_imports(root: Path) -> list[tuple[str, str, str]]:
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in ENGINE:
                found += [(path.relative_to(REPO).as_posix(), node.module, a.name) for a in node.names]
    return found


def test_every_engine_import_in_src_and_tests_resolves():
    """The grep from the workstream spec, made executable: nothing imported from the engine may disappear."""
    imports = _engine_imports(REPO / "src") + _engine_imports(REPO / "tests")
    assert {
        ("src/sed/cli.py", "sed.ingest.loader", "run_import"),
        ("src/sed/synth/generate.py", "sed.ingest.loader", "MANIFEST"),
    } <= set(imports)
    missing = [
        f"{where}: {module}.{name}"
        for where, module, name in imports
        if not hasattr(importlib.import_module(module), name)
    ]
    assert not missing, missing
