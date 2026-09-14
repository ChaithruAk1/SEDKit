"""Generated contract files must match the code (regenerate with `uv run python scripts/codegen.py`)."""

from __future__ import annotations

import json
import subprocess
import sys

from tests.conftest import REPO


def test_contracts_are_up_to_date():
    proc = subprocess.run(
        [sys.executable, "scripts/codegen.py", "--check", "--only", "cli,openapi,skills"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_cli_contract_lists_core_commands():
    tree = json.loads((REPO / "contracts" / "cli.json").read_text(encoding="utf-8"))
    names = {c["name"] for c in tree["commands"]}
    assert {"init", "doctor", "db", "synth", "import", "mappings", "alias", "report", "metrics"} <= names
