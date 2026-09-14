"""Packets: work items rendered as JSON lines, greedily batched under PacketLimits, and the run folder files.

* One item per line (so agents can page a packet with Read offset/limit), UTF-8 with LF line endings.
* Every line is at most `max_line_chars` characters (1800 by default). Over-long lines are shortened by truncating
  `desc`, then `close`, then `short` (then any other text field), each cut ending in an ellipsis.
* A batch holds at most `max_items` lines and at most `max_chars` characters including newlines.
* Refs are `T001`, `T002`, ... per batch; they map back to items only through the `ai_batch_item` table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sed.ai.contract import PacketLimits, WorkItem
from sed.ai.hashing import sha256_bytes
from sed.errors import ValidationFailed

TRUNCATION_ORDER = ("desc", "close", "short")
ELLIPSIS = "…"
SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
UNICODE_BREAKS = {chr(0x85): r"\u0085", chr(0x2028): r"\u2028", chr(0x2029): r"\u2029"}


def batch_name(seq: int) -> str:
    return f"batch_{seq:04d}"


def ref_for(index: int, width: int = 3) -> str:
    return f"T{index + 1:0{width}d}"


def _dumps(data: dict[str, Any]) -> str:
    """Compact JSON; Unicode line and paragraph separators are escaped so a packet line never splits."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    for raw, escaped in UNICODE_BREAKS.items():
        text = text.replace(raw, escaped)
    return text


def render_line(ref: str, payload: dict[str, Any], max_line_chars: int) -> str:
    """One packet line for an item, shortened to at most max_line_chars characters."""
    data: dict[str, Any] = {"ref": ref, **{k: v for k, v in payload.items() if k != "ref"}}
    line = _dumps(data)
    if len(line) <= max_line_chars:
        return line
    others = sorted(
        (k for k, v in data.items() if isinstance(v, str) and k != "ref" and k not in TRUNCATION_ORDER),
        key=lambda k: (-len(data[k]), k),
    )
    for key in [k for k in TRUNCATION_ORDER if isinstance(data.get(k), str)] + others:
        while len(line) > max_line_chars and data[key]:
            value = data[key]
            keep = max(0, len(value) - (len(line) - max_line_chars) - 1)
            data[key] = value[:keep].rstrip() + ELLIPSIS if keep > 0 else ""
            line = _dumps(data)
        if len(line) <= max_line_chars:
            return line
    for key in [k for k in data if k != "ref"]:
        data.pop(key)
        line = _dumps(data)
        if len(line) <= max_line_chars:
            return line
    raise ValidationFailed(f"Cannot fit a packet line within {max_line_chars} characters")


@dataclass
class Batch:
    seq: int
    items: list[WorkItem] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return batch_name(self.seq)

    @property
    def refs(self) -> list[str]:
        return [json.loads(line)["ref"] for line in self.lines]

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + "\n" if self.lines else ""

    @property
    def data(self) -> bytes:
        return self.text.encode("utf-8")

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.data)

    @property
    def longest_line(self) -> int:
        return max((len(line) for line in self.lines), default=0)


def plan_batches(items: list[WorkItem], limits: PacketLimits) -> list[Batch]:
    """Greedy batching in priority order: close a batch when the next line would exceed max_items or max_chars."""
    if limits.max_items < 1:
        raise ValidationFailed("PacketLimits.max_items must be at least 1")
    width = max(3, len(str(limits.max_items)))
    line_limit = min(limits.max_line_chars, limits.max_chars - 1)
    batches: list[Batch] = []
    current = Batch(seq=1)
    placeholder = "T" + "0" * width
    for item in items:
        probe = render_line(placeholder, item.payload, line_limit)
        cost = len(probe) + 1
        if current.items and (len(current.items) >= limits.max_items or current.chars + cost > limits.max_chars):
            batches.append(current)
            current = Batch(seq=current.seq + 1)
        line = render_line(ref_for(len(current.items), width), item.payload, line_limit)
        current.items.append(item)
        current.lines.append(line)
    if current.items:
        batches.append(current)
    return batches


def check_file_name(name: str, reserved: set[str]) -> str:
    if not SAFE_FILE_NAME.match(name) or name in reserved:
        raise ValidationFailed(f"Handler produced an invalid or reserved run file name '{name}'")
    return name


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF line endings (no platform newline translation)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def write_run_files(
    run_dir: Path,
    batches: list[Batch],
    context_files: dict[str, str],
    batch_files: dict[str, dict[str, str]],
    manifest_text: str,
) -> None:
    in_dir, out_dir = run_dir / "in", run_dir / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        write_text(in_dir / f"{batch.name}.jsonl", batch.text)
        for name, text in batch_files.get(batch.name, {}).items():
            write_text(in_dir / name, text)
    for name, text in context_files.items():
        write_text(in_dir / name, text)
    write_text(run_dir / "manifest.json", manifest_text)
