"""Test-only module `hello`: proves a new module plugs in (CLI, API, nav) with no core edits."""

from __future__ import annotations

from sed.modules.contract import ApiMount, CliMount, Module, NavItem

MODULE = Module(
    key="hello",
    title="Hello (test module)",
    description="Seam test for new modules",
    cli=(CliMount("hello", "tests.platform.modules.sample_module.cli:app"),),
    api=ApiMount("tests.platform.modules.sample_module.api:router"),
    nav=(NavItem("hello.home", "Hello", "/hello", 500, "hand"),),
)
