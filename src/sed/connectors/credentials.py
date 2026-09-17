"""Connector secrets, never in config, git, logs or output.

Lookup order for a secret named in the connector config (`secret: <name>`):
1. environment variable `SED_CREDENTIAL_<NAME>` (upper case, `-` and `.` as `_`), for Task Scheduler wrappers;
2. the Windows Credential Manager through `keyring` (service `sed`, user `<name>`) when keyring is installed;
3. the file `DATA_DIR/secret/connectors/<name>` (first line), readable by the user account only.
`describe` reports which source holds a secret without revealing it (doctor).
"""

from __future__ import annotations

import os
import re
from typing import Any

from sed.errors import PreconditionFailed

KEYRING_SERVICE = "sed"
NAME_RE = re.compile(r"^[a-z][a-z0-9._-]{1,59}$")


def _env_name(name: str) -> str:
    return "SED_CREDENTIAL_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _check(name: str) -> None:
    if not NAME_RE.match(name):
        raise PreconditionFailed(f"Invalid secret name '{name}' (lowercase letters, digits, '.', '_', '-')")


def _keyring(name: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def _file(paths: Any, name: str) -> str | None:
    path = paths.secret / "connectors" / name
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[0].strip() if lines and lines[0].strip() else None


def get_credential(paths: Any, name: str) -> str:
    _check(name)
    value = os.environ.get(_env_name(name)) or _keyring(name) or _file(paths, name)
    if not value:
        raise PreconditionFailed(
            f"No secret '{name}': set {_env_name(name)}, store it in the Windows Credential Manager (service "
            f"'{KEYRING_SERVICE}', user '{name}') or put it in the secret/connectors/{name} file of the data folder"
        )
    return value


def describe(paths: Any, name: str) -> str | None:
    """Where the secret is found ('environment', 'credential manager', 'file') or None; never the value."""
    _check(name)
    if os.environ.get(_env_name(name)):
        return "environment"
    if _keyring(name):
        return "credential manager"
    if _file(paths, name):
        return "file"
    return None
