"""`sed modules new <key>`: scaffold a module that passes `sed modules check`, `sed modules gate` and CI at once.

The scaffold is the smallest module with every surface wired: a manifest, a read-only API route with its model, a CLI
command, a dashboard page with a typed fixture, a test, and the module's CLAUDE.md. It also registers the module in
`BUILTIN`, `config/modules.yaml`, the web fixtures (GET handler and nav item) and the module table of
`docs/modules.md`. Nothing is overwritten: the command refuses an existing key or package, and `--dry-run` lists what
it would write. After it, run codegen (`scripts/codegen.py`) so the API types reach the web code, then the gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sed.errors import PreconditionFailed, ValidationFailed

KEY_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
ICON = "apps"


@dataclass(frozen=True)
class Names:
    key: str
    title: str
    description: str
    depends_on: tuple[str, ...]
    order: int

    @property
    def cls(self) -> str:
        return self.key[:1].upper() + self.key[1:]

    def fill(self, text: str) -> str:
        deps = "".join(f'"{d}", ' for d in self.depends_on).rstrip(", ")
        values = {
            "__KEY__": self.key,
            "__CLS__": self.cls,
            "__TITLE__": self.title,
            "__DESCRIPTION__": self.description,
            "__DEPS__": f"({deps},)" if self.depends_on else "()",
            "__ORDER__": str(self.order),
            "__ICON__": ICON,
        }
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        return text


MANIFEST = '''"""__TITLE__ module (`sed.modules.__KEY__`), scaffolded by `sed modules new`.

Describe here what the module covers, where its data comes from and which surfaces it adds; keep CLAUDE.md in this
folder up to date with the same facts for agents.
"""

from __future__ import annotations

from sed.modules.contract import ApiMount, CliMount, Module, NavItem

MODULE = Module(
    key="__KEY__",
    title="__TITLE__",
    description="__DESCRIPTION__",
    depends_on=__DEPS__,
    cli=(CliMount("__KEY__", "sed.modules.__KEY__.cli:app"),),
    api=ApiMount("sed.modules.__KEY__.api:router"),
    nav=(NavItem("__KEY__.overview", "__TITLE__", "/__KEY__", __ORDER__, "__ICON__"),),
    data_subdirs=("config/__KEY__",),
    owned_paths=(
        "src/sed/modules/__KEY__/**",
        "config/__KEY__/**",
        "web/src/modules/__KEY__/**",
        "web/src/api/fixtures/__KEY__.ts",
        "tests/modules/__KEY__/**",
    ),
)
'''

API_MODELS = '''"""__TITLE__ API models (/api/__KEY__/...)."""

from __future__ import annotations

from sed.api.models import ApiModel


class __CLS__Overview(ApiModel):
    module: str
    title: str
    data_class: str
    data_as_of: str | None
'''

API = '''"""__TITLE__ API routes mounted at /api/__KEY__. Every route reads through `deps.read_conn` (query_only)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from sed.api.deps import read_conn
from sed.modules.__KEY__.api_models import __CLS__Overview

router = APIRouter()


@router.get("/overview", response_model=__CLS__Overview)
def overview(request: Request, conn: sqlite3.Connection = Depends(read_conn)) -> __CLS__Overview:
    from sed.ingest.freshness import data_as_of
    from sed.settings import load_settings

    paths = request.app.state.paths
    day = data_as_of(conn, load_settings(paths))
    return __CLS__Overview(
        module="__KEY__",
        title="__TITLE__",
        data_class=paths.data_class,
        data_as_of=day.isoformat() if day else None,
    )
'''

CLI = '''"""`sed __KEY__ ...`: __TITLE__ in the terminal."""

from __future__ import annotations

import typer

from sed.cli_common import DataDirOpt, JsonOpt, ProfileOpt, handle_errors, paths_for
from sed.output import console, emit

app = typer.Typer(no_args_is_help=True, help="__TITLE__")


@app.command("status")
@handle_errors
def status_cmd(profile: ProfileOpt = None, data_dir: DataDirOpt = None, as_json: JsonOpt = False) -> None:
    """Whether the module is enabled for the profile."""
    from sed import modules

    paths = paths_for(profile, data_dir)
    enabled = "__KEY__" in modules.enabled_keys(paths)

    def human(p: dict) -> None:
        console().print(f"__KEY__: {'enabled' if p['enabled'] else 'disabled'} ({p['profile']})", markup=False)

    emit({"module": "__KEY__", "enabled": enabled, "profile": paths.profile}, as_json, human)
'''

MODULE_CLAUDE = """# __TITLE__ module (`sed.modules.__KEY__`)

__DESCRIPTION__

- **Manifest:** `__init__.py` (`MODULE`). Everything else is reached through the lazy import references declared there.
- **API:** `/api/__KEY__/overview` (`api.py`, `api_models.py`), read-only.
- **CLI:** `sed __KEY__ status`.
- **Dashboard pages:** `web/src/modules/__KEY__/` (`#/__KEY__`), with fixtures in `web/src/api/fixtures/__KEY__.ts`.
- **Config:** `config/__KEY__/`, overridable in `DATA_DIR\\config\\__KEY__\\`. Real values live only there.
- **Tests:** `tests/modules/__KEY__/`.
- **Boundary:** core code never imports this package directly (`tests/platform/test_core_boundaries.py`).
"""

CONFIG_README = """# config/__KEY__

Synthetic defaults of the __TITLE__ module. Every file here can be overridden at the same relative path in
`DATA_DIR\\config\\__KEY__\\`; real values (names, ids, thresholds) go only there, never in git.
"""

TEST_INIT = ""

TEST = '''"""__TITLE__ module scaffold: the manifest is valid, and the API route and CLI command answer."""

from __future__ import annotations

import json

from sed import modules
from tests.fixtures.api import api_client


def test_manifest_is_registered_and_valid():
    module = modules.get("__KEY__")
    assert module.title == "__TITLE__" and modules.validate() == []
    assert [item.path for item in module.nav] == ["/__KEY__"]


def test_overview_route_and_cli(ops_profile):
    body = api_client(ops_profile.paths).get("/api/__KEY__/overview").json()
    assert body["module"] == "__KEY__" and body["data_class"] == "synthetic"

    from typer.testing import CliRunner

    from sed.cli import app

    result = CliRunner().invoke(app, ["__KEY__", "status", "--data-dir", str(ops_profile.paths.data_dir), "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout.strip().splitlines()[-1])["module"] == "__KEY__"
'''

WEB_INDEX = """/** __TITLE__ web module: manifest discovered by src/modules/registry.ts. */
import type { WebModule } from '../types';
import { __KEY_UPPER___ROUTES } from './routes';

const __KEY__ = {
  key: '__KEY__',
  title: '__TITLE__',
  routes: __KEY_UPPER___ROUTES,
} satisfies WebModule<'__KEY__'>;

export default __KEY__;
"""

WEB_ROUTES = """/** __TITLE__ pages (`#/__KEY__/...`), each loaded lazily. */
import type { WebRoute } from '../types';

export const __KEY_UPPER___ROUTES = [
  {
    path: '__KEY__',
    title: '__TITLE__',
    filters: ['as_of'],
    load: () => import('./pages/__CLS__OverviewPage'),
  },
] as const satisfies readonly WebRoute<'__KEY__'>[];
"""

WEB_PAGE = """import { Stack, Text } from '@mantine/core';

import { useApi } from '../../../api/useApi';
import { ErrorState } from '../../../components/ErrorState';
import { formatDate } from '../../../components/format';
import { PageHeader } from '../../../components/PageHeader';
import { SectionCard } from '../../../components/SectionCard';

export default function __CLS__OverviewPage() {
  const overview = useApi('/api/__KEY__/overview', {});
  const data = overview.data;

  return (
    <Stack gap="md">
      <PageHeader title="__TITLE__" description={data ? `Data as of ${formatDate(data.data_as_of)}` : undefined} />
      {overview.error ? <ErrorState error={overview.error} onRetry={overview.reload} /> : null}
      <SectionCard title="Module">
        <Text size="sm">{data ? `${data.title} (${data.module}), ${data.data_class} data` : 'Loading…'}</Text>
      </SectionCard>
    </Stack>
  );
}
"""

WEB_FIXTURE = """/** __TITLE__ module API fixtures. Fictional data only. */
import type { Schema } from '../types';
import { AS_OF } from './random';

export function overview(): Schema<'__CLS__Overview'> {
  return { module: '__KEY__', title: '__TITLE__', data_class: 'synthetic', data_as_of: AS_OF };
}
"""


def _names(key: str, title: str | None, description: str | None, depends_on: list[str], order: int) -> Names:
    if not KEY_RE.match(key):
        raise ValidationFailed(f"Module key '{key}' must match {KEY_RE.pattern} (lowercase letters and digits)")
    title = (title or key.capitalize()).strip()
    if not title or any(c in title for c in "\"'\\`{}<>$"):
        raise ValidationFailed("The title must be plain text without quotes, braces or backslashes")
    description = (description or f"{title}: describe what the module covers").strip()
    if any(c in description for c in "\"'\\`{}<>$"):
        raise ValidationFailed("The description must be plain text without quotes, braces or backslashes")
    return Names(key, title, description, tuple(depends_on), order)


def _insert_before(text: str, anchor: str, addition: str, where: str) -> str:
    if anchor not in text:
        raise PreconditionFailed(f"Cannot register the module in {where}: anchor {anchor.strip()!r} not found")
    return text.replace(anchor, addition + anchor, 1)


def plan(
    root: Path,
    key: str,
    *,
    title: str | None = None,
    description: str | None = None,
    depends_on: list[str] | None = None,
    order: int = 90,
) -> dict[str, str]:
    """Every file the scaffold writes or edits (repo-relative path -> new content). Raises when the key is taken."""
    from sed import modules

    n = _names(key, title, description, list(depends_on or []), order)
    installed = {m.key for m in modules.installed()}
    reserved = modules.CORE_PAGE_KEYS | modules.CORE_CLI_NAMES | modules.CORE_API_KEYS
    if key in installed or key in reserved:
        raise PreconditionFailed(f"Module key '{key}' is already used")
    unknown = [d for d in n.depends_on if d not in installed]
    if unknown:
        raise ValidationFailed(f"Unknown dependencies: {', '.join(unknown)}", {"installed": sorted(installed)})
    package = root / "src" / "sed" / "modules" / key
    if package.exists():
        raise PreconditionFailed(f"{package.as_posix()} already exists")

    upper = key.upper()

    def web(text: str) -> str:
        return n.fill(text.replace("__KEY_UPPER__", upper))

    files = {
        f"src/sed/modules/{key}/__init__.py": n.fill(MANIFEST),
        f"src/sed/modules/{key}/api_models.py": n.fill(API_MODELS),
        f"src/sed/modules/{key}/api.py": n.fill(API),
        f"src/sed/modules/{key}/cli.py": n.fill(CLI),
        f"src/sed/modules/{key}/CLAUDE.md": n.fill(MODULE_CLAUDE),
        f"config/{key}/README.md": n.fill(CONFIG_README),
        f"tests/modules/{key}/__init__.py": TEST_INIT,
        f"tests/modules/{key}/test_{key}_module.py": n.fill(TEST),
        f"web/src/modules/{key}/index.ts": web(WEB_INDEX),
        f"web/src/modules/{key}/routes.tsx": web(WEB_ROUTES),
        f"web/src/modules/{key}/pages/{n.cls}OverviewPage.tsx": web(WEB_PAGE),
        f"web/src/api/fixtures/{key}.ts": web(WEB_FIXTURE),
    }
    for rel in files:
        if (root / rel).exists():
            raise PreconditionFailed(f"{rel} already exists")

    def read(rel: str) -> str:
        path = root / rel
        if not path.is_file():
            raise PreconditionFailed(f"{rel} is missing; run `sed modules new` from the repository root")
        return path.read_text(encoding="utf-8")

    registry = read("src/sed/modules/__init__.py")
    match = re.search(r"^BUILTIN = \((?P<body>[^)]*)\)", registry, flags=re.MULTILINE)
    if not match:
        raise PreconditionFailed("Cannot find BUILTIN in src/sed/modules/__init__.py")
    body = match.group("body").rstrip().rstrip(",")
    files["src/sed/modules/__init__.py"] = (
        registry[: match.start("body")] + f'{body}, "sed.modules.{key}"' + registry[match.end("body") :]
    )

    enabled = read("config/modules.yaml")
    m = re.search(r"^enabled:\s*\[(?P<list>[^\]]*)\]", enabled, flags=re.MULTILINE)
    if not m:
        raise PreconditionFailed("config/modules.yaml has no one-line `enabled: [...]` list")
    items = [x.strip() for x in m.group("list").split(",") if x.strip()]
    files["config/modules.yaml"] = enabled[: m.start("list")] + ", ".join([*items, key]) + enabled[m.end("list") :]

    index = read("web/src/api/fixtures/index.ts")
    imports = list(re.finditer(r"^import \* as \w+ from '\./\w+';\n", index, flags=re.MULTILINE))
    if not imports:
        raise PreconditionFailed("Cannot find the fixture imports in web/src/api/fixtures/index.ts")
    at = imports[-1].end()
    index = index[:at] + f"import * as {key} from './{key}';\n" + index[at:]
    index = _insert_before(
        index,
        "};\n\nconst POST_HANDLERS",
        f"  '/api/{key}/overview': () => {key}.overview(),\n",
        "web/src/api/fixtures/index.ts",
    )
    files["web/src/api/fixtures/index.ts"] = index

    core = read("web/src/api/fixtures/core.ts")
    files["web/src/api/fixtures/core.ts"] = _insert_before(
        core,
        "      { id: 'core.review'",
        f"      {{ id: '{key}.overview', module: '{key}', label: '{n.title}', path: '/{key}', order: {order}, "
        f"icon: '{ICON}' }},\n",
        "web/src/api/fixtures/core.ts (nav)",
    )

    docs = read("docs/modules.md")
    rows = list(re.finditer(r"^\| #(\d+) \| `[a-z0-9]+` \|.*\|\n", docs, flags=re.MULTILINE))
    if rows:
        last = rows[-1]
        row = f"| #{int(last.group(1)) + 1} | `{key}` | {n.description} (`src/sed/modules/{key}/CLAUDE.md`) |\n"
        files["docs/modules.md"] = docs[: last.end()] + row + docs[last.end() :]
    return files


def write(root: Path, files: dict[str, str]) -> list[str]:
    written = []
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(rel)
    return written
