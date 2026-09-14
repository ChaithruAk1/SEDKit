"""Confluence space HTML export reader: Data Center markup, zip input, script/style skipping, error messages."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sed.errors import ValidationFailed
from sed.ingest.confluence import COLUMNS, read_confluence_export

DC_PAGE = """<!DOCTYPE html><html><head><title>LEDGER : Month-end batch runbook</title>
<style>.x { color: red }</style><script>var tracker = "do not index";</script></head>
<body>
<div id="main-header"><h1 id="title-heading">Month-end batch runbook</h1></div>
<div class="page-metadata">
  Created by <span class="author">Robin Vale</span>, last updated by Sam Ortiz on Jul 14, 2026
</div>
<div id="main-content" class="wiki-content group">
  <h2>Symptoms</h2><p>Posting job LDG-EOD fails on business day 1.</p>
  <ul><li>Check the queue</li><li>Restart the scheduler</li></ul>
  <script>alert("x")</script>
</div>
<div class="labels"><a class="aui-label-split-main" href="#">runbook</a>
<a class="aui-label-split-main" href="#">batch</a>
<a class="aui-label-split-main" href="#">runbook</a></div>
</body></html>"""


def _space(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html><body><ul><li>pages</li></ul></body></html>", encoding="utf-8")
    (root / "Month-end-batch-runbook_884201.html").write_text(DC_PAGE, encoding="utf-8")
    (root / "attachments.html").write_text("<html><body>no page id</body></html>", encoding="utf-8")
    return root


def _row(table) -> dict:
    assert table.columns == COLUMNS
    assert len(table.rows) == 1
    return dict(zip(table.columns, table.rows[0], strict=True))


def test_data_center_page_fields(tmp_path: Path):
    row = _row(read_confluence_export(_space(tmp_path / "confluence_space_LEDGER")))
    assert row["page_id"] == "884201"
    assert row["space_key"] == "LEDGER" and row["title"] == "Month-end batch runbook"
    assert row["author"] == "Robin Vale"
    assert row["last_updated"] == "Jul 14, 2026"
    assert row["labels"] == "runbook, batch"
    assert "Posting job LDG-EOD fails" in row["body"] and "Restart the scheduler" in row["body"]
    assert "alert" not in row["body"] and "do not index" not in row["body"]


def test_zip_export_matches_directory(tmp_path: Path):
    space = _space(tmp_path / "export" / "LEDGER")
    archive = tmp_path / "ledger_export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for f in space.iterdir():
            zf.write(f, f"LEDGER/{f.name}")
    assert _row(read_confluence_export(archive)) == _row(read_confluence_export(space))


def test_space_key_falls_back_to_directory_name(tmp_path: Path):
    space = _space(tmp_path / "confluence-HRCORE")
    page = space / "Month-end-batch-runbook_884201.html"
    page.write_text(DC_PAGE.replace("LEDGER : ", ""), encoding="utf-8")
    assert _row(read_confluence_export(space))["space_key"] == "HRCORE"


@pytest.mark.parametrize("kind", ["dir-without-index", "zip-without-index", "plain-file"])
def test_not_a_confluence_export(tmp_path: Path, kind: str):
    if kind == "dir-without-index":
        target = tmp_path / "space"
        target.mkdir()
    elif kind == "zip-without-index":
        target = tmp_path / "space.zip"
        with zipfile.ZipFile(target, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
    else:
        target = tmp_path / "page.html"
        target.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValidationFailed):
        read_confluence_export(target)
