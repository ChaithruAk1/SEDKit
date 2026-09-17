"""Onboarding real exports: profile an unfamiliar file, try a draft mapping, save validated config overrides.

Used by `sed mappings draft | try | save-override` and `sed config save-override`, and by the `sed-map-export` skill.
Nothing here prints raw cell values of an export except through the normal mapping dry-run (which applies each field's
PII class) or, in the file profile, for low-cardinality columns that do not look like people (see `_safe_values`).

Overrides go to DATA_DIR\\config at the same relative path as the repo default. A save validates the effective
(layered) result and restores the previous file when validation fails, so a bad draft never breaks imports.
"""

from __future__ import annotations

import difflib
import json
import re
import secrets
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sed import modules as registry
from sed.errors import SedError, ValidationFailed
from sed.ingest.mapping import (
    MappingSpec,
    glob_matches,
    header_match_score,
    load_all_mappings,
    load_mapping,
    read_for_mapping,
    resolve_columns,
)
from sed.ingest.readers import read_table
from sed.ingest.transforms import TRANSFORMS
from sed.paths import Paths
from sed.settings import config_sha256, deep_merge, load_layered, read_yaml, repo_config_dir

DRAFT_PREFIX = "map-"
PROFILE_ROWS = 5000  # rows looked at per column for the profile
MAX_CATEGORIES = 15
MAX_CATEGORY_CHARS = 40
PLATFORM_CONFIG = ("settings.yaml", "pii.yaml", "fx.yaml", "modules.yaml", "aliases.yaml")
PERSON_HEADER_RE = re.compile(
    r"assign|caller|opened.?by|requested|requester|owner|manager|user|contact|approver|created.?by|updated.?by|"
    r"resolved.?by|closed.?by|author|reporter|email|e-mail|phone|mobile|\bname\b|person|employee|watcher",
    re.IGNORECASE,
)
PERSON_VALUE_RE = re.compile(
    r"^[A-ZÀ-Þ][a-zß-ÿ'\-]+(\s+[A-ZÀ-Þ][a-zß-ÿ'\-]+){1,2}$"  # Firstname Lastname
    r"|^[A-ZÀ-Þ][a-zß-ÿ'\-]+,\s*[A-ZÀ-Þ][a-zß-ÿ'\-]+$"  # Lastname, Firstname
    r"|@|\+?\d[\d\s().-]{7,}"  # email or phone
)
SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("boolean", re.compile(r"^(true|false|yes|no|y|n)$", re.IGNORECASE)),
    ("integer", re.compile(r"^-?\d+$")),
    ("decimal", re.compile(r"^-?\d{1,3}([.,\s]\d{3})*([.,]\d+)?$|^-?\d+[.,]\d+$")),
    ("datetime YYYY-MM-DD HH:MM[:SS]", re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")),
    ("date YYYY-MM-DD", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("datetime D/M/YYYY HH:MM[:SS]", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}")),
    ("date D/M/YYYY", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("datetime D.M.YYYY HH:MM", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}")),
    ("date D.M.YYYY", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")),
    ("datetime DD/Mon/YY h:mm AM (Jira)", re.compile(r"^\d{1,2}/[A-Za-z]{3}/\d{2}\s+\d{1,2}:\d{2}\s*[AP]M$")),
    ("date DD/Mon/YY (Jira)", re.compile(r"^\d{1,2}/[A-Za-z]{3}/\d{2}$")),
    ("duration", re.compile(r"^\d+\s+(day|days|hour|hours|minute|minutes|jour|jours|heure|heures)\b", re.IGNORECASE)),
)


# -- file profile --------------------------------------------------------------------------------------------------


def _shape(value: str) -> str:
    v = value.strip()
    for name, pattern in SHAPES:
        if pattern.search(v):
            return name
    if "@" in v:
        return "email-like"
    if len(v) <= 24 and " " not in v:
        return "code " + re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", v))
    return "text"


def _safe_values(header: str, values: list[str]) -> list[str] | None:
    """Distinct values of a low-cardinality column, unless the header or a value looks like a person."""
    distinct = sorted(set(values))
    if not distinct or len(distinct) > MAX_CATEGORIES or PERSON_HEADER_RE.search(header):
        return None
    if any(len(v) > MAX_CATEGORY_CHARS or PERSON_VALUE_RE.search(v) for v in distinct):
        return None
    return distinct


def candidates(path: Path, paths: Paths) -> list[dict[str, Any]]:
    """Every effective mapping scored against the file, best first (glob match, then header score)."""
    out: list[dict[str, Any]] = []
    for spec in load_all_mappings(paths).values():
        try:
            table = read_for_mapping(path, spec)
        except SedError as exc:
            out.append({"mapping": spec.name, "target": spec.target, "error": exc.message})
            continue
        index, missing = resolve_columns(spec, table.columns)
        out.append(
            {
                "mapping": spec.name,
                "target": spec.target,
                "glob_match": glob_matches(spec, path.name),
                "score": round(header_match_score(spec, table.columns), 3),
                "min_score": spec.match.min_score,
                "missing_required": missing,
                "mapped_fields": {field: table.columns[i] for field, i in sorted(index.items())},
                "unmapped_columns": [c for i, c in enumerate(table.columns) if i not in index.values()][:40],
                "reader_warnings": table.warnings,
            }
        )
    out.sort(key=lambda r: (not r.get("glob_match", False), -r.get("score", 0)))
    return out


def profile_file(path: Path, paths: Paths) -> dict[str, Any]:
    """Header names, fill rate, distinct count and value shapes per column; categories only for safe columns."""
    if not path.exists():
        raise ValidationFailed(f"File not found: {path}")
    known = {h for spec in load_all_mappings(paths).values() for h in spec.known_headers()}
    table = read_table(path, None, known)
    rows = table.rows[:PROFILE_ROWS]
    columns = []
    for i, header in enumerate(table.columns):
        values = [str(r[i]).strip() for r in rows if i < len(r) and r[i] is not None and str(r[i]).strip()]
        shapes = Counter(_shape(v) for v in values)
        columns.append(
            {
                "column": header,
                "filled_pct": round(100.0 * len(values) / len(rows), 1) if rows else 0.0,
                "distinct": len(set(values)),
                "shapes": dict(shapes.most_common(3)),
                "max_chars": max((len(v) for v in values), default=0),
                "categories": _safe_values(str(header), values),
            }
        )
    return {
        "file": path.name,
        "rows": len(table.rows),
        "profiled_rows": len(rows),
        "reader": {
            "encoding": table.encoding,
            "delimiter": table.delimiter,
            "sheet": table.sheet,
            "header_row": table.header_row,
            "warnings": table.warnings,
        },
        "columns": columns,
        "candidates": candidates(path, paths),
    }


def new_draft(path: Path, paths: Paths) -> dict[str, Any]:
    """A draft folder runs/map-<stamp>/ with in/profile.json (for an agent) and an empty out/."""
    profile = profile_file(path, paths)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    folder = paths.runs / f"{DRAFT_PREFIX}{stamp}-{secrets.token_hex(2)}"
    (folder / "in").mkdir(parents=True, exist_ok=False)
    (folder / "out").mkdir()
    (folder / "in" / "profile.json").write_bytes((json.dumps(profile, indent=2, ensure_ascii=False) + "\n").encode())
    best = next((c for c in profile["candidates"] if "error" not in c), None)
    name = best["mapping"] if best else "new_mapping"
    return {
        "draft_dir": folder.as_posix(),
        "profile": (folder / "in" / "profile.json").as_posix(),
        "out": (folder / "out" / f"{name}.yaml").as_posix(),
        "best_candidate": best,
    }


# -- drafts --------------------------------------------------------------------------------------------------------


def _repo_layer(rel: str) -> dict[str, Any]:
    return load_layered(rel, None) if (repo_config_dir() / rel).is_file() else {}


def load_mapping_draft(draft: Path, paths: Paths) -> MappingSpec:
    """The effective mapping a draft file would give once saved as an override (not saved)."""
    data = read_yaml(draft)
    parent = data.pop("extends", None)
    name = str(data.get("name") or parent or draft.stem)
    index = registry.mapping_index(paths)
    if parent:
        if parent not in index:
            raise ValidationFailed(f"Draft extends unknown mapping '{parent}'", {"available": sorted(index)})
        rel = f"{index[parent][1]}/{parent}.yaml"
        # Saved under its own name, a draft replaces the local layer; under another name it builds on the full result.
        base = _repo_layer(rel) if parent == name else load_layered(rel, paths)
        merged = deep_merge(base, data)
    else:
        merged = data
    merged["name"] = name
    try:
        spec = MappingSpec.model_validate({**merged, "sha256": config_sha256(merged)})
    except ValidationError as exc:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid mapping draft '{name}'", errors) from exc
    targets = registry.ingest_targets(paths)
    if spec.target not in targets:
        raise ValidationFailed(
            f"Mapping draft '{name}' names unknown target '{spec.target}'", {"targets": sorted(targets)}
        )
    return spec


# -- overrides -----------------------------------------------------------------------------------------------------


def _diff(previous: str | None, new: str, label: str) -> str:
    lines = difflib.unified_diff(
        (previous or "").splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"{label} (current)" if previous is not None else f"{label} (none)",
        tofile=f"{label} (draft)",
    )
    return "".join(lines)


def _draft_text(draft: Path) -> str:
    """The draft's text with LF line endings (saved files are always LF)."""
    return draft.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


def _write_checked(target: Path, text: str, validate: Any) -> dict[str, Any]:
    """Write `text` to `target`, run `validate()`, restore the previous content when it raises."""
    previous = target.read_bytes() if target.is_file() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
    try:
        result = validate() or {}
    except SedError:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)
        raise
    backup = None
    if previous is not None:
        backup_path = target.with_name(target.name + ".prev")
        backup_path.write_bytes(previous)
        backup = backup_path.as_posix()
    return {"previous_backup": backup, **result}


def save_mapping_override(paths: Paths, draft: Path, *, module: str | None = None, dry_run: bool = False) -> dict:
    spec = load_mapping_draft(draft, paths)
    index = registry.mapping_index(paths)
    dirs = dict(registry.mapping_dirs(paths))
    if spec.name in index:
        key, rel = index[spec.name]
        if module and module != key:
            raise ValidationFailed(f"Mapping '{spec.name}' belongs to module '{key}', not '{module}'")
    else:
        if not module:
            raise ValidationFailed(
                f"'{spec.name}' is a new mapping: pass --module to choose its folder", {"modules": sorted(dirs)}
            )
        if module not in dirs:
            raise ValidationFailed(f"Module '{module}' is not enabled or has no mappings", {"modules": sorted(dirs)})
        key, rel = module, dirs[module]
    target = paths.config / rel / f"{spec.name}.yaml"
    text = _draft_text(draft)
    out = {
        "mapping": spec.name,
        "module": key,
        "target": target.as_posix(),
        "created": not target.is_file(),
        "diff": _diff(
            target.read_text(encoding="utf-8") if target.is_file() else None, text, f"{rel}/{spec.name}.yaml"
        ),
        "dry_run": dry_run,
        "required_fields": spec.required_fields(),
    }
    if dry_run:
        return out

    def validate() -> dict[str, Any]:
        saved = load_mapping(spec.name, paths)
        return {"sha256": saved.sha256, "overlaps": registry.mapping_glob_overlaps(paths)}

    return {**out, **_write_checked(target, text, validate)}


def _config_owner(rel: str) -> str:
    if rel in PLATFORM_CONFIG:
        return "platform"
    if rel.startswith("templates/") and rel.endswith(".map.yaml") and rel.count("/") == 1:
        return "templates"
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts) or ":" in rel:
        raise ValidationFailed(f"'{rel}' is not a relative config path")
    key = parts[0]
    installed = {m.key for m in registry.installed()}
    if "/" not in rel or key not in installed or not rel.endswith(".yaml"):
        raise ValidationFailed(
            f"'{rel}' is not an overridable config file",
            {"platform": list(PLATFORM_CONFIG), "modules": sorted(f"{k}/..." for k in installed)},
        )
    if rel.startswith(f"{key}/mappings/"):
        raise ValidationFailed("Mappings are saved with `sed mappings save-override`")
    return key


def _validate_config(paths: Paths, rel: str, owner: str) -> dict[str, Any]:
    from sed.ingest.pii import PiiConfig
    from sed.settings import load_settings

    settings = load_settings(paths)
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(settings.reporting_tz)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError for malformed keys
        raise ValidationFailed(f"settings.yaml: unknown reporting_tz '{settings.reporting_tz}'") from exc
    warnings: list[str] = []
    if rel == "modules.yaml":
        registry.enabled_keys(paths)
    elif rel == "pii.yaml":
        PiiConfig.from_dict(load_layered("pii.yaml", paths))
    elif rel == "fx.yaml":
        rates = load_layered("fx.yaml", paths).get("rates") or {}
        bad = [k for k, v in rates.items() if not isinstance(v, int | float)]
        if bad:
            raise ValidationFailed("fx.yaml: rates must be numbers", bad)
    elif rel == "aliases.yaml":
        data = load_layered("aliases.yaml", paths)
        bad = [k for k, v in data.items() if not isinstance(v, dict)]
        if bad:
            raise ValidationFailed("aliases.yaml: each kind must map raw names to target ids", bad)
    elif owner == "templates":
        from sed.reports.template_map import load_template_map

        load_template_map(rel.split("/", 1)[1].removesuffix(".map.yaml"), paths)
    elif owner != "platform":
        if owner not in registry.enabled_keys(paths):
            warnings.append(f"module '{owner}' is disabled: its checks did not run")
        for check in registry.doctor_checks(paths):
            if check.name == f"{owner}.config_valid" and check.status == "fail":
                raise ValidationFailed(f"{rel}: module '{owner}' config is invalid", check.detail)
            if check.name.startswith(f"{owner}.") and check.status != "ok":
                warnings.append(f"{check.name}: {check.detail}")
    return {"warnings": warnings}


def save_config_override(paths: Paths, rel: str, draft: Path, *, dry_run: bool = False) -> dict[str, Any]:
    rel = rel.replace("\\", "/").strip("/")
    owner = _config_owner(rel)
    read_yaml(draft)  # must parse as a YAML mapping
    text = _draft_text(draft)
    target = paths.config / rel
    out = {
        "config": rel,
        "owner": owner,
        "target": target.as_posix(),
        "created": not target.is_file(),
        "diff": _diff(target.read_text(encoding="utf-8") if target.is_file() else None, text, rel),
        "dry_run": dry_run,
    }
    if dry_run:
        return out
    return {**out, **_write_checked(target, text, lambda: _validate_config(paths, rel, owner))}


# -- skill reference -----------------------------------------------------------------------------------------------


def canonical_fields_markdown() -> str:
    """reference/canonical_fields.md of the sed-map-export skill: targets and their default mappings (generated)."""
    targets = registry.ingest_targets(None)
    by_target: dict[str, list[MappingSpec]] = {}
    for spec in load_all_mappings(None).values():
        by_target.setdefault(spec.target, []).append(spec)
    index = registry.mapping_index(None)
    lines = [
        "# Canonical targets and default mappings (generated by scripts/codegen.py; never edit)",
        "",
        "Each import target is a canonical table. A mapping feeds one target: `fields` map canonical field names to",
        "source columns (`from`, first match wins, header comparison ignores case, accents and punctuation), with a",
        "transform and a mandatory PII class (`none`, `person` = pseudonymised, `free_text` = scrubbed).",
        "",
        "Transforms: " + ", ".join(f"`{t}`" for t in sorted(TRANSFORMS)),
        "",
    ]
    for name in sorted(targets):
        target = targets[name]
        lines += [f"## `{name}`", ""]
        if target.table:
            lines.append(f"Table `{target.table}`, key ({', '.join(f'`{k}`' for k in target.key)}).")
        else:
            lines.append("Registry-only target (no table of its own).")
        lines.append("")
        for spec in sorted(by_target.get(name, []), key=lambda m: m.name):
            module = index.get(spec.name, ("?", ""))[0]
            lines += [
                f"### mapping `{spec.name}` (module {module}, load mode `{spec.load_mode}`)",
                "",
                f"File globs: {', '.join(f'`{g}`' for g in spec.match.glob)}.",
                "",
                "| field | from (first aliases) | transform | pii | required |",
                "|---|---|---|---|---|",
            ]
            for field, fspec in spec.fields.items():
                aliases = ", ".join(f"`{a}`" for a in fspec.from_[:4]) + (" …" if len(fspec.from_) > 4 else "")
                required = "yes" if fspec.required else ""
                lines.append(f"| `{field}` | {aliases} | `{fspec.transform}` | `{fspec.pii}` | {required} |")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
