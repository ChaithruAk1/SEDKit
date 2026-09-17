"""Module registry: discovers installed modules, applies enablement and resolves their declared surfaces.

Built-in modules are listed in BUILTIN. Tests (and later, local development) may add packages through the
SED_EXTRA_MODULES environment variable (comma-separated; only `tests.` and `sed.modules.` packages are accepted).
Enablement comes from the layered `config/modules.yaml` (`enabled: [ops]`); extra modules are always enabled.

This package imports only sed.errors, sed.paths, sed.settings and the contract, so the CLI stays fast. Module code is
reached only through `load_ref`.
"""

from __future__ import annotations

import fnmatch
import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sed.errors import PreconditionFailed, ValidationFailed
from sed.modules.contract import (
    EXTENSION_POINT_RE,
    IMPORT_REF_RE,
    MODULE_KEY_RE,
    AliasKind,
    EntityRef,
    Extension,
    Module,
    NavItem,
    ReportDef,
    SkillDef,
)
from sed.paths import Paths
from sed.settings import load_layered, repo_config_dir

BUILTIN = ("sed.modules.ops", "sed.modules.sap", "sed.modules.delivery")
EXTRA_ENV = "SED_EXTRA_MODULES"
EXTRA_PREFIXES = ("tests.", "sed.modules.")
CORE_CLI_NAMES = frozenset(
    {
        "version",
        "init",
        "doctor",
        "db",
        "data",
        "modules",
        "synth",
        "import",
        "mappings",
        "alias",
        "inbox",
        "report",
        "ai",
        "review",
        "serve",
        "config",
        "pull",
        "schedule",
        "sources",
        "branding",
    }
)
# Skills that belong to the platform rather than a module (no run handler, no module surface).
CORE_SKILLS = ("sed-map-export", "sed-review", "sed-eval", "sed-review-module", "sed-build-module")
CORE_NAV = (
    NavItem("core.review", "Review", "/review", 800, "list-check"),
    NavItem("core.runs", "AI runs", "/runs", 810, "robot"),
    NavItem("core.reports", "Reports", "/reports", 820, "file-text"),
    NavItem("core.data", "Data", "/data", 900, "database"),
)
# First path segment of every core page: module keys must not take them (pages are #/<key>/...).
CORE_PAGE_KEYS = frozenset(item.path.strip("/").split("/")[0] for item in CORE_NAV)
CORE_TABLES = (
    "meta",
    "import_batch",
    "row_reject",
    "alias",
    "unmapped_value",
    "person_key",
    "person_display",
    "finding",
    "review_decision",
    "report_snapshot",
    "report_artifact",
    "job",
    "ai_run",
    "ai_batch",
    "ai_batch_item",
    "ai_sample",
    "ai_group_member",
)
PORTFOLIO_TABLES = (
    "vendor",
    "application",
    "work_item",
    "doc_page",
)  # core-owned schema; any module may write via ingest

_override: tuple[tuple[Module, ...], frozenset[str] | None] | None = None
_cache: dict[tuple[str, ...], tuple[Module, ...]] = {}


# -- discovery ---------------------------------------------------------------------------------------------------


def _extra_packages() -> tuple[str, ...]:
    names = tuple(n.strip() for n in os.environ.get(EXTRA_ENV, "").split(",") if n.strip())
    bad = [n for n in names if not n.startswith(EXTRA_PREFIXES)]
    if bad:
        raise ValidationFailed(f"{EXTRA_ENV} only accepts packages under {EXTRA_PREFIXES}", bad)
    return names


def _load_package(package: str) -> Module:
    try:
        mod = importlib.import_module(package)
    except ImportError as exc:
        raise ValidationFailed(f"Cannot import module package '{package}': {exc}") from exc
    module = getattr(mod, "MODULE", None)
    if not isinstance(module, Module):
        raise ValidationFailed(f"Package '{package}' does not define MODULE: Module")
    return module


def installed(*, include_extra: bool = True) -> tuple[Module, ...]:
    if _override is not None:
        return _override[0]
    packages = BUILTIN + (_extra_packages() if include_extra else ())
    if packages not in _cache:
        _cache[packages] = tuple(_load_package(p) for p in packages)
    return _cache[packages]


def enabled_keys(paths: Paths | None = None) -> frozenset[str]:
    if _override is not None:
        mods, keys = _override
        return keys if keys is not None else frozenset(m.key for m in mods)
    config = load_layered("modules.yaml", paths)  # a YAML error is a validation error, never "enable everything"
    base = {m.key for m in installed(include_extra=False)}
    if "enabled" not in config:
        keys = base
    else:
        listed = config["enabled"]
        if not isinstance(listed, list) or not all(isinstance(k, str) for k in listed):
            raise ValidationFailed(
                "config/modules.yaml: 'enabled' must be a list of module keys", {"enabled": repr(listed)[:200]}
            )
        keys = set(listed)
    extra = {m.key for m in installed()} - base
    return frozenset(keys | extra)


def enabled(paths: Paths | None = None) -> tuple[Module, ...]:
    keys = enabled_keys(paths)
    return tuple(m for m in installed() if m.key in keys)


def get(key: str) -> Module:
    for m in installed():
        if m.key == key:
            return m
    raise ValidationFailed(f"Unknown module '{key}'", {"installed": [m.key for m in installed()]})


def require_enabled(paths: Paths | None, key: str) -> Module:
    module = get(key)
    if key not in enabled_keys(paths):
        raise PreconditionFailed(f"Module '{key}' is disabled; enable it in config/modules.yaml")
    return module


@contextmanager
def use_modules(mods: tuple[Module, ...] | list[Module], enabled_keys: set[str] | None = None) -> Iterator[None]:
    """Temporarily replace the installed modules (tests)."""
    global _override
    previous = _override
    _override = (tuple(mods), frozenset(enabled_keys) if enabled_keys is not None else None)
    try:
        yield
    finally:
        _override = previous


def load_ref(ref: str) -> Any:
    if not IMPORT_REF_RE.match(ref or ""):
        raise ValidationFailed(f"Invalid import reference '{ref}' (expected 'package.module:attr')")
    module_name, attr = ref.split(":", 1)
    try:
        return getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ValidationFailed(f"Cannot resolve '{ref}': {exc}") from exc


# -- surfaces ----------------------------------------------------------------------------------------------------


def report(key: str) -> tuple[Module, ReportDef]:
    for m in installed():
        for r in m.reports:
            if r.key == key:
                return m, r
    available = sorted(r.key for m in installed() for r in m.reports)
    raise ValidationFailed(f"Unknown report '{key}'", {"available": available})


def reports(paths: Paths | None = None) -> list[tuple[Module, ReportDef]]:
    return [(m, r) for m in enabled(paths) for r in m.reports]


def skill(name: str) -> tuple[Module, SkillDef]:
    for m in installed():
        for s in m.skills:
            if s.name == name:
                return m, s
    raise ValidationFailed(
        f"Unknown skill '{name}'", {"available": sorted(s.name for m in installed() for s in m.skills)}
    )


def handler(name: str) -> Any:
    _, sdef = skill(name)
    if not sdef.handler:
        raise ValidationFailed(f"Skill '{name}' has no run handler")
    obj = load_ref(sdef.handler)
    return obj() if isinstance(obj, type) else obj


def nav(paths: Paths | None = None) -> list[tuple[str, NavItem]]:
    items = [("core", item) for item in CORE_NAV]
    items += [(m.key, item) for m in enabled(paths) for item in m.nav]
    return sorted(items, key=lambda kv: (kv[1].order, kv[1].id))


def metric_definitions(paths: Paths | None = None) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for m in enabled(paths):
        if not m.metric_definitions:
            continue
        for key, value in dict(load_ref(m.metric_definitions)).items():
            if key in out:
                raise ValidationFailed(f"Metric definition '{key}' declared twice (module {m.key})")
            out[key] = (value[0], value[1])
    return out


def mapping_dirs(paths: Paths | None = None) -> list[tuple[str, str]]:
    return [(m.key, m.mappings_dir) for m in enabled(paths) if m.mappings_dir]


def _mapping_names(rel: str, paths: Paths | None) -> set[str]:
    roots = [repo_config_dir() / rel]
    if paths is not None:
        roots.append(paths.config / rel)
    return {p.stem for root in roots if root.is_dir() for p in root.glob("*.yaml")}


def mapping_index(paths: Paths | None = None) -> dict[str, tuple[str, str]]:
    """Mapping name -> (module key, config-relative dir), from repo defaults and DATA_DIR overrides."""
    index: dict[str, tuple[str, str]] = {}
    for key, rel in mapping_dirs(paths):
        for name in sorted(_mapping_names(rel, paths)):
            if name in index and index[name][0] != key:
                raise ValidationFailed(f"Mapping '{name}' is declared by modules {index[name][0]} and {key}")
            index[name] = (key, rel)
    return index


def mapping_owners(paths: Paths | None = None) -> dict[str, set[str]]:
    """Mapping name -> keys of the installed modules, enabled or not, whose mapping folders hold it (never raises)."""
    owners: dict[str, set[str]] = {}
    for m in installed():
        if m.mappings_dir:
            for name in _mapping_names(m.mappings_dir, paths):
                owners.setdefault(name, set()).add(m.key)
    return owners


def ingest_targets(paths: Paths | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    owner: dict[str, str] = {}
    for m in enabled(paths):
        if not m.ingest_targets:
            continue
        for name, target in dict(load_ref(m.ingest_targets)).items():
            if name in out:
                raise ValidationFailed(f"Ingest target '{name}' is declared by modules {owner[name]} and {m.key}")
            out[name] = target
            owner[name] = m.key
    return out


def ingest_hooks(paths: Paths | None = None) -> list[Any]:
    hooks = []
    for m in enabled(paths):
        if m.ingest_hooks:
            hook = load_ref(m.ingest_hooks)
            if hook is not None:
                hooks.append(hook)
    return hooks


def entities() -> dict[str, EntityRef]:
    """Entity key -> EntityRef over the installed modules; two different definitions of one key are an error."""
    out: dict[str, EntityRef] = {}
    for m in installed():
        for e in m.entities:
            if e.key in out and out[e.key] != e:
                raise ValidationFailed(f"Entity '{e.key}' is declared differently by two modules")
            out[e.key] = e
    return out


def alias_kinds() -> tuple[str, ...]:
    return tuple(a.key for m in installed() for a in m.alias_kinds)


def finding_kinds() -> tuple[str, ...]:
    """Finding kinds over the installed modules. The finding table no longer checks them; each module owns its own."""
    return tuple(k for m in installed() for k in m.finding_kinds)


def entity_for_alias_kind(kind: str) -> EntityRef:
    for m in installed():
        for a in m.alias_kinds:
            if a.key == kind:
                return entities()[a.entity]
    raise ValidationFailed(f"Unknown alias kind '{kind}'", {"available": list(alias_kinds())})


def extensions(point: str, paths: Paths | None = None) -> list[tuple[str, Any]]:
    """(module key, resolved reference) of every enabled module's contribution to `point` ("<owner>.<name>"), in module
    order. The owner of the point defines what the references resolve to."""
    return [(m.key, load_ref(e.ref)) for m in enabled(paths) for e in m.extensions if e.point == point]


def doctor_checks(paths: Paths) -> list[Any]:
    from sed.doctor import Check

    out: list[Any] = []
    for m in enabled(paths):
        if not m.doctor_checks:
            continue
        try:
            out.extend(load_ref(m.doctor_checks)(paths))
        except Exception as exc:  # a module check must never crash doctor
            out.append(Check(f"{m.key}.crashed", "fail", f"{type(exc).__name__}: {exc}"))
    return out


def mapping_glob_overlaps(paths: Paths | None = None) -> list[str]:
    """Warnings for file globs that two different modules would both match."""
    from sed.ingest.mapping import load_all_mappings

    index = mapping_index(paths)
    globs: list[tuple[str, str, str]] = []
    for name, spec in load_all_mappings(paths).items():
        module = index.get(name, ("?", ""))[0]
        globs += [(module, name, g) for g in spec.match.glob]
    out = []
    for i, (mod_a, name_a, glob_a) in enumerate(globs):
        for mod_b, name_b, glob_b in globs[i + 1 :]:
            if mod_a != mod_b and (fnmatch.fnmatch(glob_a, glob_b) or fnmatch.fnmatch(glob_b, glob_a)):
                out.append(f"{mod_a}:{name_a} ({glob_a}) overlaps {mod_b}:{name_b} ({glob_b})")
    return out


# -- validation --------------------------------------------------------------------------------------------------


def declared_refs(m: Module) -> list[str]:
    refs = [c.app for c in m.cli] + [r.builder for r in m.reports] + [r.markdown for r in m.reports if r.markdown]
    refs += [s.handler for s in m.skills if s.handler]
    refs += [x for x in (m.ingest_targets, m.ingest_hooks, m.metric_definitions, m.doctor_checks, m.rule_findings) if x]
    refs += [e.ref for e in m.extensions]
    if m.api:
        refs.append(m.api.router)
    if m.synth:
        refs.append(m.synth.generate)
    return refs


def validate(mods: tuple[Module, ...] | list[Module] | None = None) -> list[str]:
    """Static checks of module declarations (no imports). Returns a list of problems; empty means valid."""
    mods = tuple(installed() if mods is None else mods)
    problems: list[str] = []
    spaces = ("module", "report", "skill", "nav", "cli", "table", "alias", "entity", "finding kind")
    seen: dict[str, dict[str, str]] = {k: {} for k in spaces}

    def claim(space: str, name: str, owner: str) -> None:
        if name in seen[space]:
            problems.append(f"duplicate {space} '{name}' ({seen[space][name]} and {owner})")
        else:
            seen[space][name] = owner

    keys = {m.key for m in mods}
    for m in mods:
        if not MODULE_KEY_RE.match(m.key):
            problems.append(f"module key '{m.key}' must match {MODULE_KEY_RE.pattern}")
        if m.key in CORE_PAGE_KEYS:
            problems.append(f"module key '{m.key}' is reserved by the core page #/{m.key}")
        claim("module", m.key, m.key)
        problems += [f"{m.key}: missing dependency '{d}'" for d in m.depends_on if d not in keys]
        problems += [f"{m.key}: invalid import reference '{r}'" for r in declared_refs(m) if not IMPORT_REF_RE.match(r)]
        for r in m.reports:
            claim("report", r.key, m.key)
            if ("md" in r.formats) != bool(r.markdown):
                problems.append(f"{m.key}: report '{r.key}' must declare markdown iff 'md' is a format")
            if not r.spec.startswith(f"{m.key}/"):
                problems.append(f"{m.key}: report spec '{r.spec}' must live under config/{m.key}/")
        for s in m.skills:
            claim("skill", s.name, m.key)
            if not s.name.startswith("sed-"):
                problems.append(f"{m.key}: skill '{s.name}' must be prefixed 'sed-'")
        for item in m.nav:
            claim("nav", item.id, m.key)
            if not item.id.startswith(f"{m.key}."):
                problems.append(f"{m.key}: nav id '{item.id}' must start with '{m.key}.'")
            if item.path != f"/{m.key}" and not item.path.startswith(f"/{m.key}/"):
                problems.append(f"{m.key}: nav path '{item.path}' must be /{m.key} or under /{m.key}/")
        for name in [c.name for c in m.cli] + list(m.legacy_cli):
            claim("cli", name, m.key)
            if name in CORE_CLI_NAMES:
                problems.append(f"{m.key}: CLI name '{name}' is reserved by the core")
        for table in m.tables:
            claim("table", table, m.key)
            if table in CORE_TABLES or table in PORTFOLIO_TABLES:
                problems.append(f"{m.key}: table '{table}' is core-owned")
        for e in m.entities:
            claim("entity", e.key, m.key)
        entity_keys = {e.key for e in m.entities} | {e.key for other in mods for e in other.entities}
        for a in m.alias_kinds:
            claim("alias", a.key, m.key)
            if a.entity not in entity_keys:
                problems.append(f"{m.key}: alias kind '{a.key}' points at unknown entity '{a.entity}'")
        for k in m.finding_kinds:
            claim("finding kind", k, m.key)
        if m.rule_findings and not m.finding_kinds:
            problems.append(f"{m.key}: rule_findings needs the finding kinds it computes in finding_kinds")
        problems += _extension_problems(m, mods)
        if m.mappings_dir and not m.mappings_dir.startswith(f"{m.key}/"):
            problems.append(f"{m.key}: mappings_dir must live under config/{m.key}/")
        problems += [
            f"{m.key}: config file '{c}' must live under {m.key}/"
            for c in m.config_files
            if not c.startswith(f"{m.key}/")
        ]
        problems += [
            f"{m.key}: data subdir '{d}' must live under config/{m.key}"
            for d in m.data_subdirs
            if d != f"config/{m.key}" and not d.startswith(f"config/{m.key}/")
        ]
    return problems


def _extension_problems(m: Module, mods: tuple[Module, ...]) -> list[str]:
    problems = [
        f"{m.key}: invalid extension point name '{p}'" for p in m.extension_points if not EXTENSION_POINT_RE.match(p)
    ]
    problems += [
        f"{m.key}: extension point '{p}' is declared twice"
        for p in sorted({p for p in m.extension_points if m.extension_points.count(p) > 1})
    ]
    owners = {x.key: x for x in mods}
    seen: set[str] = set()
    for e in m.extensions:
        owner_key, _, name = e.point.partition(".")
        owner = owners.get(owner_key)
        if e.point in seen:
            problems.append(f"{m.key}: extension point '{e.point}' is contributed to twice")
        seen.add(e.point)
        if owner is None or (owner_key != m.key and owner_key not in m.depends_on):
            problems.append(f"{m.key}: extension '{e.point}' needs '{owner_key}' as this module or a dependency")
        elif name not in owner.extension_points:
            problems.append(f"{m.key}: module '{owner_key}' declares no extension point '{name}'")
    return problems


__all__ = [
    "BUILTIN",
    "CORE_CLI_NAMES",
    "CORE_NAV",
    "CORE_SKILLS",
    "CORE_TABLES",
    "EXTRA_ENV",
    "PORTFOLIO_TABLES",
    "AliasKind",
    "Extension",
    "Module",
    "alias_kinds",
    "declared_refs",
    "doctor_checks",
    "enabled",
    "enabled_keys",
    "entities",
    "entity_for_alias_kind",
    "extensions",
    "finding_kinds",
    "get",
    "handler",
    "ingest_hooks",
    "ingest_targets",
    "installed",
    "load_ref",
    "mapping_dirs",
    "mapping_glob_overlaps",
    "mapping_index",
    "mapping_owners",
    "metric_definitions",
    "nav",
    "report",
    "reports",
    "require_enabled",
    "skill",
    "use_modules",
    "validate",
]
