"""Confluence space HTML export -> RawTable (one row per page).

Accepts the export directory (containing index.html and page files) or a .zip of it. Parses, per page: page id
(trailing digits of the file name), space key (title prefix or directory name), title, author, last-modified
date, labels and visible body text. Tolerant of both Cloud and Data Center export markup.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from sed.errors import ValidationFailed
from sed.ingest.readers import RawTable

COLUMNS = ["page_id", "space_key", "title", "author", "last_updated", "labels", "body"]


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.labels: list[str] = []
        self.metadata = ""
        self.body_parts: list[str] = []
        self._in_title = False
        self._depth_main = 0
        self._in_label = False
        self._in_meta = 0
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        classes = a.get("class", "").split()
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style"}:
            self._skip += 1
        if self._depth_main:
            self._depth_main += 1
        elif a.get("id") == "main-content" or "wiki-content" in classes:
            self._depth_main = 1
        if self._in_meta:
            self._in_meta += 1
        elif "page-metadata" in classes:
            self._in_meta = 1
        if tag == "a" and ("label" in classes or "aui-label-split-main" in classes):
            self._in_label = True
        if tag in {"p", "br", "li", "tr", "h1", "h2", "h3", "div"} and self._depth_main:
            self.body_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        if self._depth_main:
            self._depth_main -= 1
        if self._in_meta:
            self._in_meta -= 1
        if tag == "a":
            self._in_label = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif self._in_label and data.strip():
            self.labels.append(data.strip())
        elif self._in_meta:
            self.metadata += data
        elif self._depth_main:
            self.body_parts.append(data)


_META_AUTHOR = re.compile(r"Created by\s+(.+?)(?:,|\s+on\s|\s+last\s|$)", re.IGNORECASE)
_META_DATE = re.compile(
    r"(?:last modified|modified|last updated)(?:\s+by\s+.+?)?\s+on\s+([A-Z][a-z]{2} \d{1,2}, \d{4})"
)


def _parse_page(path: Path, space_hint: str) -> list[str | None] | None:
    m = re.search(r"(\d{3,})\.html?$", path.name)
    if not m:
        return None
    parser = _PageParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    raw_title = re.sub(r"\s+", " ", parser.title).strip()
    space_key, title = space_hint, raw_title
    if " : " in raw_title:
        space_key, title = (part.strip() for part in raw_title.split(" : ", 1))
    meta = re.sub(r"\s+", " ", parser.metadata)
    author = _META_AUTHOR.search(meta)
    modified = _META_DATE.search(meta)
    body = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(parser.body_parts))).strip()
    return [
        m.group(1),
        space_key,
        title,
        author.group(1).strip() if author else None,
        modified.group(1) if modified else None,
        ", ".join(dict.fromkeys(parser.labels)) or None,
        body or None,
    ]


def read_confluence_export(path: Path) -> RawTable:
    if path.is_file() and path.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
            roots = [p for p in Path(tmp).rglob("index.html")]
            if not roots:
                raise ValidationFailed(f"{path.name}: no index.html inside the Confluence export zip")
            return _read_dir(roots[0].parent, str(path))
    if path.is_dir():
        if not (path / "index.html").is_file():
            raise ValidationFailed(f"{path.name}: not a Confluence HTML export (index.html missing)")
        return _read_dir(path, str(path))
    raise ValidationFailed(f"{path.name}: expected a Confluence export directory or .zip")


def _read_dir(root: Path, source: str) -> RawTable:
    space_hint = re.sub(r"^confluence[_-]?(space[_-]?)?", "", root.name, flags=re.IGNORECASE) or root.name
    rows = []
    for page in sorted(root.glob("*.htm*")):
        if page.name.lower() == "index.html":
            continue
        parsed = _parse_page(page, space_hint)
        if parsed:
            rows.append(parsed)
    return RawTable(list(COLUMNS), rows, source, "utf-8", None, None, 1, [f"{len(rows)} Confluence pages parsed"])
