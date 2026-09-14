"""Migration hygiene: every schema file from 003 declares an owner; pending files only add indexes."""

from __future__ import annotations

import re

from sed import db
from tests.conftest import REPO

SCHEMA = REPO / "src" / "sed" / "schema"
OWNER = re.compile(r"^-- owner: [a-z][a-z0-9]*\s*$", re.MULTILINE)


def test_numbered_migrations_are_contiguous_and_owned():
    files = sorted(p for p in SCHEMA.glob("*.sql") if re.match(r"^\d{3}_", p.name))
    numbers = [int(p.name[:3]) for p in files]
    assert numbers == list(range(1, len(files) + 1))
    for path in files:
        if int(path.name[:3]) >= 3:
            first = path.read_text(encoding="utf-8").splitlines()[0]
            assert OWNER.match(first), f"{path.name} must start with '-- owner: <core|module key>'"


def test_pending_migrations_only_create_indexes():
    pending = SCHEMA / "pending"
    for path in sorted(pending.glob("*.sql")) if pending.is_dir() else []:
        text = path.read_text(encoding="utf-8")
        assert OWNER.search(text.splitlines()[0]), path.name
        for statement in db.split_sql(text):
            assert re.match(r"^\s*CREATE INDEX IF NOT EXISTS\b", statement, re.IGNORECASE), (path.name, statement)


def test_pending_dir_is_ignored_by_migrate():
    assert all(name.endswith(".sql") and "/" not in name for _, name, _ in db.migrations())
    assert db.latest_version() == len([p for p in SCHEMA.glob("*.sql") if re.match(r"^[0-9]{3}_", p.name)])
