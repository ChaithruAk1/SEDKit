from __future__ import annotations

from datetime import date

from sed import db
from sed.ingest.aliases import alias_targets, resolve_target_id
from sed.ingest.freshness import data_as_of, import_freshness
from sed.settings import load_settings


def test_data_as_of_is_the_export_moment(ops_profile):
    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        assert data_as_of(conn, load_settings(ops_profile.paths)) == date(2026, 9, 1)
        rows = import_freshness(conn)
        assert rows and all(r["files"] >= 1 for r in rows)
    finally:
        conn.close()


def test_alias_target_helpers(ops_profile):
    conn = db.connect(ops_profile.paths.db, readonly=True)
    try:
        vendor = ops_profile.ids["vendor_p2"]
        assert resolve_target_id(conn, "vendor", vendor) == vendor
        name = conn.execute("SELECT name FROM vendor WHERE vendor_id = ?", (vendor,)).fetchone()[0]
        assert resolve_target_id(conn, "vendor", name.upper()) == vendor
        hits = alias_targets(conn, "vendor", name[:5])
        assert {"id": vendor, "name": name} in hits
    finally:
        conn.close()
