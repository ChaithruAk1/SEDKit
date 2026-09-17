"""Uploads (W8): safe names, allowed types and content, size caps, zip member paths, the synthetic-profile guard, and
the API flow (token, job, import of exactly the uploaded file)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from sed.errors import PreconditionFailed, ValidationFailed
from sed.ingest import upload as U


class _Paths:
    def __init__(self, root: Path, data_class: str = "real") -> None:
        self.inbox = root / "inbox"
        self.data_class = data_class


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("incident_2026-08.csv", "incident_2026-08.csv"),
        ("C:\\Users\\x\\Downloads\\Contracts Register.xlsx", "Contracts_Register.xlsx"),
        ("../../secret/pii_salt.txt", "pii_salt.txt"),
        ("rapport été 2026.csv", "rapport_t_2026.csv"),
    ],
)
def test_safe_name_keeps_only_the_file_name(raw, clean):
    assert U.safe_name(raw) == clean


@pytest.mark.parametrize("raw", ["", "..", ".csv", "noextension", "///"])
def test_safe_name_refuses_names_without_a_file_name_and_extension(raw):
    with pytest.raises(ValidationFailed):
        U.safe_name(raw)


def test_types_content_size_and_synthetic_guard(tmp_path, monkeypatch):
    paths = _Paths(tmp_path)
    with pytest.raises(ValidationFailed, match="Unsupported file type"):
        U.save_upload(paths, "run.ps1", b"Write-Host hi")
    with pytest.raises(ValidationFailed, match="wrong content"):
        U.save_upload(paths, "costs.xlsx", b"number,short_description\n")
    with pytest.raises(ValidationFailed, match="workbook"):
        U.save_upload(paths, "incident.csv", b"PK\x03\x04rest")
    with pytest.raises(ValidationFailed, match="empty"):
        U.save_upload(paths, "incident.csv", b"")
    monkeypatch.setattr(U, "MAX_BYTES", 10)
    with pytest.raises(ValidationFailed, match="larger"):
        U.save_upload(paths, "incident.csv", b"number,short_description\n")
    with pytest.raises(PreconditionFailed, match="synthetic profile"):
        U.save_upload(_Paths(tmp_path, "synthetic"), "incident.csv", b"a\n")


def test_saves_without_overwriting(tmp_path):
    paths = _Paths(tmp_path)
    first = U.save_upload(paths, "incident.csv", b"number\nINC1\n")
    second = U.save_upload(paths, "incident.csv", b"number\nINC2\n")
    assert (first["file"], second["file"]) == ("incident.csv", "incident_2.csv")
    assert (paths.inbox / "incident.csv").read_bytes() == b"number\nINC1\n"
    assert not [p for p in paths.inbox.iterdir() if p.name.startswith("~$")]


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_confluence_zip_is_unpacked_from_its_index_folder(tmp_path):
    paths = _Paths(tmp_path)
    data = _zip({"SPACE/index.html": b"<html></html>", "SPACE/page_1.html": b"<html>p</html>", "readme.txt": b"x"})
    saved = U.save_upload(paths, "space export.zip", data)
    folder = paths.inbox / "space_export"
    assert saved["kind"] == "folder" and saved["file"] == "space_export"
    assert sorted(p.name for p in folder.iterdir()) == ["index.html", "page_1.html"]


@pytest.mark.parametrize(
    "members",
    [
        {"../evil/index.html": b"x"},
        {"index.html": b"x", "C:/Windows/evil.html": b"x"},
        {"a/index.html": b"x", "a/../../evil.html": b"x"},
        {"page.html": b"no index"},
    ],
)
def test_unsafe_or_foreign_zips_are_refused(tmp_path, members):
    paths = _Paths(tmp_path)
    with pytest.raises(ValidationFailed):
        U.save_upload(paths, "space.zip", _zip(members))
    assert not paths.inbox.exists() or not list(paths.inbox.iterdir())
