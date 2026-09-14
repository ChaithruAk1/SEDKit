"""`sed` starts fast: importing the CLI (including module mounts) must not pull in heavy libraries."""

from __future__ import annotations

import json
import subprocess
import sys

from tests.conftest import REPO

HEAVY = ("pandas", "numpy", "fastapi", "starlette", "uvicorn", "pptx", "xlsxwriter", "sklearn", "rapidfuzz", "faker")


def test_cli_import_is_light():
    code = f"import json, sys; import sed.cli; print(json.dumps(sorted(m for m in {HEAVY!r} if m in sys.modules)))"
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True
    )
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
