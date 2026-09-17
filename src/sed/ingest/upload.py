"""Uploaded export files (W8): the dashboard's alternative to dropping files into the inbox by hand.

`save_upload` checks one uploaded file (name, type, size, content), stores it in the profile's inbox and returns its
path; the caller then imports exactly that file with the normal import (`sed.ingest.loader.run_import`), so uploads
go through the same mappings, PII scrubbing, hooks and data-quality checks as files dropped by hand or pulled by a
connector. A `.zip` upload must be a Confluence space HTML export (a folder with `index.html`): it is unpacked into a
folder in the inbox with every member path checked.

Rules:
* names are reduced to a safe file name (letters, digits, `._-`), never a path; an existing inbox entry is not
  overwritten (a numbered suffix is added);
* only table and export types SED reads: csv, tsv, txt, xlsx, xlsm, xls, xlsb, ods, zip;
* a size cap per file (`MAX_BYTES`) and, for zips, on the unpacked size and member count;
* the synthetic profile imports only generator files, so an upload there needs `synthetic_ok` (the person confirms
  the file is a hand-made fictional fixture), exactly like `sed import --allow-unmanifested`.
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from sed.errors import PreconditionFailed, ValidationFailed
from sed.paths import Paths

MAX_BYTES = 200 * 1024 * 1024
MAX_UNZIPPED_BYTES = 500 * 1024 * 1024
MAX_MEMBERS = 20_000
TABLE_SUFFIXES = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".xlsb", ".ods")
SUFFIXES = (*TABLE_SUFFIXES, ".zip")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls


def safe_name(name: str) -> str:
    """The last path segment of `name` with unsafe characters replaced; refuses empty, hidden or dotted-only names."""
    base = re.split(r"[\\/]", name or "")[-1].strip()
    cleaned = _UNSAFE.sub("_", base).strip("._")
    stem, dot, suffix = cleaned.rpartition(".")
    if not dot or not stem or not suffix:
        raise ValidationFailed(f"Upload name '{name}' needs a file name with an extension, e.g. incident_2026-08.csv")
    if len(cleaned) > 120:
        cleaned = f"{stem[: 119 - len(suffix)]}.{suffix}"
    return cleaned


def _check_content(name: str, data: bytes) -> None:
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xlsb", ".ods", ".zip") and not data.startswith(_ZIP_MAGIC):
        raise ValidationFailed(f"{name} is not a {suffix} file (wrong content)")
    if suffix == ".xls" and not (data.startswith(_OLE_MAGIC) or data.startswith(_ZIP_MAGIC)):
        raise ValidationFailed(f"{name} is not an .xls workbook (wrong content)")
    if suffix in (".csv", ".tsv", ".txt") and data.startswith((_ZIP_MAGIC, _OLE_MAGIC)):
        raise ValidationFailed(f"{name} is a workbook, not a text export: save it with its own extension")


def free_path(inbox: Path, name: str) -> Path:
    """`inbox/name`, or `inbox/<stem>_<n><suffix>` when that name (or a folder of the stem) is taken."""
    target = inbox / name
    stem, suffix = target.stem, target.suffix
    counter = 1
    while target.exists() or target.with_suffix("").exists():
        counter += 1
        target = inbox / f"{stem}_{counter}{suffix}"
    return target


def write_atomic(target: Path, data: bytes) -> None:
    """Write through a `~$` staging name (skipped by the inbox scan) and rename, so an import never sees half a file."""
    part = target.with_name(f"~$upload-{target.name}")
    part.write_bytes(data)
    part.replace(target)


def _unzip(data: bytes, folder: Path) -> list[str]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValidationFailed("The .zip upload is not a valid zip archive") from exc
    members = [m for m in archive.infolist() if not m.is_dir()]
    if len(members) > MAX_MEMBERS:
        raise ValidationFailed(f"The .zip upload has more than {MAX_MEMBERS} files")
    if sum(m.file_size for m in members) > MAX_UNZIPPED_BYTES:
        raise ValidationFailed(f"The .zip upload unpacks to more than {MAX_UNZIPPED_BYTES // (1024 * 1024)} MB")
    names = []
    for member in members:
        parts = PurePosixPath(member.filename.replace("\\", "/")).parts
        if not parts or any(p in ("", ".", "..") or ":" in p for p in parts) or member.filename.startswith(("/", "\\")):
            raise ValidationFailed(f"The .zip upload has an unsafe member path: {member.filename!r}")
        names.append("/".join(parts))
    # A Confluence space export is often wrapped in one top folder: unpack from the folder that holds index.html.
    roots = sorted({n.rsplit("/", 1)[0] if "/" in n else "" for n in names if n.rsplit("/", 1)[-1] == "index.html"})
    if not roots:
        raise ValidationFailed("A .zip upload must be a Confluence space HTML export (with index.html)")
    root = min(roots, key=len)
    prefix = f"{root}/" if root else ""
    folder.mkdir(parents=True)
    written = []
    for member, name in zip(members, names, strict=True):
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix) :]
        target = folder.joinpath(*rel.split("/"))
        if folder.resolve() not in target.resolve().parents:
            raise ValidationFailed(f"The .zip upload has an unsafe member path: {member.filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source:
            target.write_bytes(source.read(MAX_UNZIPPED_BYTES + 1))
        written.append(rel)
    return written


def save_upload(paths: Paths, name: str, data: bytes, *, synthetic_ok: bool = False) -> dict[str, object]:
    """Validate and store one uploaded file in the inbox; returns {file, path, bytes, kind}."""
    if paths.data_class != "real" and not synthetic_ok:
        raise PreconditionFailed(
            "The synthetic profile imports generator files only. Upload real exports to the real profile, or confirm "
            "that this is a hand-made fictional fixture (synthetic_ok)."
        )
    clean = safe_name(name)
    suffix = Path(clean).suffix.lower()
    if suffix not in SUFFIXES:
        raise ValidationFailed(f"Unsupported file type '{suffix}' (allowed: {', '.join(SUFFIXES)})")
    if not data:
        raise ValidationFailed(f"{clean} is empty")
    if len(data) > MAX_BYTES:
        raise ValidationFailed(f"{clean} is larger than {MAX_BYTES // (1024 * 1024)} MB")
    _check_content(clean, data)
    inbox = paths.inbox
    inbox.mkdir(parents=True, exist_ok=True)
    if suffix == ".zip":
        folder = free_path(inbox, Path(clean).stem + ".zip").with_suffix("")
        staging = folder.with_name(f"~$upload-{folder.name}")  # skipped by the inbox scan until the rename
        shutil.rmtree(staging, ignore_errors=True)
        try:
            files = _unzip(data, staging)
            staging.rename(folder)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {"file": folder.name, "path": folder, "bytes": len(data), "kind": "folder", "members": len(files)}
    target = free_path(inbox, clean)
    write_atomic(target, data)
    return {"file": target.name, "path": target, "bytes": len(data), "kind": "file", "members": 1}
