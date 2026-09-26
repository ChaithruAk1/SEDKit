"""Profile and DATA_DIR resolution.

DATA_DIR lives outside the repo at <data root>\\<profile>. The data root is SED_DATA_ROOT when set, else
%LOCALAPPDATA%\\sed (not redirected by OneDrive Known Folder Move). Set SED_DATA_ROOT where %LOCALAPPDATA% is
redirected into an app's private storage, as it is inside the Claude desktop app (see docs/data-location.md), and in
tests. Resolution order: --data-dir / --profile flag, then SED_PROFILE, then 'synthetic'.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from sed.errors import PreconditionFailed

DEFAULT_PROFILE = "synthetic"
PROFILE_RE = re.compile(r"^(synthetic|real|eval-[a-z0-9]+|test-[a-z0-9-]+)$")
MOVED_MARKER = "moved.json"  # written into an old data root by `sed data move`

SUBDIRS = (
    "inbox",
    "inbox/processed",
    "inbox/rejected",
    "runs",
    "out",
    "backups",
    "logs",
    "config",
    "config/templates",
    "secret",
    "jobs",
    "audit",
)


def repo_root() -> Path:
    """Repo root: SED_REPO_ROOT, else the directory holding this package's pyproject.toml."""
    env = os.environ.get("SED_REPO_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "sed").is_dir():
            return parent
    raise PreconditionFailed("Cannot locate the sed repo root; set SED_REPO_ROOT.")


def data_root() -> Path:
    env = os.environ.get("SED_DATA_ROOT")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "sed"
    return Path.home() / ".local" / "share" / "sed"


def resolve_profile(flag: str | None = None) -> str:
    profile = flag or os.environ.get("SED_PROFILE") or DEFAULT_PROFILE
    if not PROFILE_RE.match(profile):
        raise PreconditionFailed(
            f"Invalid profile '{profile}'. Use synthetic, real, eval-<seed> or test-<name>.",
        )
    return profile


def data_class_for(profile: str) -> str:
    return "real" if profile == "real" else "synthetic"


@dataclass(frozen=True)
class Paths:
    profile: str
    data_dir: Path

    @property
    def data_class(self) -> str:
        return data_class_for(self.profile)

    @property
    def db(self) -> Path:
        return self.data_dir / "sed.db"

    @property
    def inbox(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def processed(self) -> Path:
        return self.inbox / "processed"

    @property
    def rejected(self) -> Path:
        return self.inbox / "rejected"

    @property
    def runs(self) -> Path:
        return self.data_dir / "runs"

    @property
    def out(self) -> Path:
        return self.data_dir / "out"

    @property
    def backups(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs(self) -> Path:
        return self.data_dir / "logs"

    @property
    def config(self) -> Path:
        return self.data_dir / "config"

    @property
    def secret(self) -> Path:
        return self.data_dir / "secret"

    @property
    def salt_file(self) -> Path:
        return self.secret / "pii_salt.txt"

    @property
    def ground_truth(self) -> Path:
        return self.data_dir / "ground_truth"

    @property
    def jobs(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def serve_lock(self) -> Path:
        return self.data_dir / "serve.lock"

    @property
    def audit(self) -> Path:
        """The audit trail (sed.audit): its own database, kept apart from sed.db, never pruned."""
        return self.data_dir / "audit"

    def ensure(self) -> None:
        for sub in SUBDIRS:
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)
        if self.data_class == "synthetic":
            self.ground_truth.mkdir(parents=True, exist_ok=True)


def get_paths(profile: str | None = None, data_dir: str | Path | None = None) -> Paths:
    prof = resolve_profile(profile)
    if data_dir:
        return Paths(profile=prof, data_dir=Path(data_dir))
    root = data_root()
    moved = moved_to(root)
    if moved is not None:
        raise PreconditionFailed(
            f"SED data was moved from {root} to {moved}. Set SED_DATA_ROOT={moved} for your Windows account, then "
            "restart the programs that run sed (see docs/data-location.md)."
        )
    return Paths(profile=prof, data_dir=root / prof)


def moved_to(root: Path) -> Path | None:
    """The new data root recorded in an old one by `sed data move`, else None."""
    marker = root / MOVED_MARKER
    if not marker.is_file():
        return None
    try:
        return Path(json.loads(marker.read_text(encoding="utf-8"))["moved_to"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PreconditionFailed(f"{marker} marks this data root as moved but cannot be read: {exc}") from exc


def guard_root() -> Path:
    """Folder for the commit-guard denylist (never committed)."""
    return data_root() / "guard"


# ---------------------------------------------------------------------------
# Location checks (used by doctor): DATA_DIR must be local, not synced, not in a git tree.
# ---------------------------------------------------------------------------


def is_unc(path: Path) -> bool:
    s = str(path)
    return s.startswith("\\\\") or s.startswith("//")


def onedrive_roots() -> list[Path]:
    roots = []
    for var in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        val = os.environ.get(var)
        if val:
            roots.append(Path(val))
    return roots


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def is_under_onedrive(path: Path) -> bool:
    if any(_is_relative_to(path, root) for root in onedrive_roots()):
        return True
    return any(part.lower().startswith("onedrive") for part in path.resolve().parts)


def enclosing_git_tree(path: Path) -> Path | None:
    p = path.resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def app_package_location(path: Path) -> Path | None:
    """Where Windows really keeps `path` when it sits in an app package's private storage, else None.

    Packaged (MSIX) apps such as the Claude desktop app get a private copy of %LOCALAPPDATA%: folders that the app, or
    any program it starts, creates there really live in %LOCALAPPDATA%\\Packages\\<package>\\LocalCache. Programs
    started outside the app (your own terminal, Explorer, Task Scheduler) do not see them, and removing or resetting
    the app deletes them. Only the nearest existing folder can be checked.
    """
    logical = Path(os.path.abspath(path))
    existing = next((p for p in (logical, *logical.parents) if p.exists()), None)
    if existing is None:
        return None
    try:
        physical = existing.resolve(strict=True)
    except OSError:
        return None
    parts = [part.lower() for part in physical.parts]
    for i in range(len(parts) - 4):
        if parts[i : i + 3] == ["appdata", "local", "packages"] and parts[i + 4] == "localcache":
            return physical / logical.relative_to(existing)
    return None
