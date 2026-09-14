"""Fake sed-triage-batch agent: labels a packet deterministically, like a batch agent would, for offline tests.

`label_packet(packet, context, mode=...)` reads packet text (JSON lines) and in/context.md text and returns the
output document an agent would Write to out/<batch>.json. Modes other than `valid` produce one specific defect so
tests can prove ingest rejects the whole batch:

* valid            every ref once, taxonomy codes from context.md
* missing_ref      the last ref is left out
* dup_ref          the first item appears twice
* invented_ref     an extra item with a ref that is not in the packet
* bad_category     the first item uses a category that is not in the taxonomy
* bad_subcategory  the first item uses a subcategory that does not belong to its category
* bad_confidence   the first item has confidence 1.5
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

MODES = ("valid", "missing_ref", "dup_ref", "invented_ref", "bad_category", "bad_subcategory", "bad_confidence")

KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("access", ("access", "login", "log in", "password", "sso", "locked", "authoriz", "role", "permission")),
    ("integration", ("interface", "queue", "message", "api", "transfer", "edi", "sync", "idoc")),
    ("performance", ("slow", "performance", "hangs", "freez")),
    ("batch_job", ("job", "batch", "month-end", "report")),
    ("infrastructure", ("server", "network", "certificate", "disk", "storage", "printer", "print", "vpn")),
    ("data_quality", ("wrong", "missing", "duplicate", "incorrect", "master data")),
    ("how_to", ("how to", "how do", "question", "training", "documentation")),
    ("defect", ("error", "bug", "crash", "fails", "exception", "broken")),
]


def parse_taxonomy(context: str) -> dict[str, list[str]]:
    """Category code -> subcategory codes, read from the Taxonomy section of in/context.md."""
    section = context.split("## Taxonomy", 1)[1] if "## Taxonomy" in context else ""
    section = section.split("\n## ", 1)[0]
    categories: dict[str, list[str]] = {}
    current = None
    for line in section.splitlines():
        top = re.match(r"^- `([^`]+)`:", line)
        if top:
            current = top.group(1)
            categories[current] = []
        elif current and line.strip().startswith("- subcategories:"):
            categories[current] = re.findall(r"`([^`]+)`", line)
    return categories


def _category(text: str, taxonomy: dict[str, list[str]]) -> str:
    lowered = text.lower()
    for code, words in KEYWORDS:
        if code in taxonomy and any(w in lowered for w in words):
            return code
    return "other" if "other" in taxonomy else next(iter(taxonomy))


def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()[:5]
    return "_".join(words)[:60] or "unspecified_symptom"


def _confidence(ref: str, text: str) -> float:
    digest = int(hashlib.sha256(f"{ref}|{text}".encode()).hexdigest()[:6], 16)
    return round(0.3 + (digest % 66) / 100, 2)


def label_packet(packet: str, context: str, *, mode: str = "valid", model: str = "fake-agent-1") -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; use one of {MODES}")
    taxonomy = parse_taxonomy(context)
    if not taxonomy:
        raise ValueError("context has no taxonomy section")
    items = []
    for line in packet.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        text = " ".join(str(row.get(k, "")) for k in ("short", "desc", "close"))
        category = _category(text, taxonomy)
        subs = taxonomy[category]
        items.append(
            {
                "ref": row["ref"],
                "am_category": category,
                "am_subcategory": subs[0] if subs else None,
                "symptom_key": _slug(str(row.get("short") or text)),
                "misfiled_as": "request" if "request" in text.lower() and category == "access" else "none",
                "confidence": _confidence(row["ref"], text),
                "rationale": f"Keywords in the ticket text point to {category}.",
            }
        )
    if not items:
        raise ValueError("packet has no lines")
    if mode == "missing_ref":
        items = items[:-1]
    elif mode == "dup_ref":
        items.append(dict(items[0]))
    elif mode == "invented_ref":
        items.append({**items[0], "ref": "T999"})
    elif mode == "bad_category":
        items[0]["am_category"] = "not_a_category"
    elif mode == "bad_subcategory":
        items[0]["am_subcategory"] = "not_a_subcategory"
    elif mode == "bad_confidence":
        items[0]["confidence"] = 1.5
    return {"meta": {"model": model}, "items": items}


def write_output(path: Any, document: dict[str, Any]) -> None:
    """Write an agent output file the way the Write tool does (UTF-8, LF)."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
