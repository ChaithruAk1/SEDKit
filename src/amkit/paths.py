"""Profile and DATA_DIR resolution.

DATA_DIR lives outside the repo at %LOCALAPPDATA%\\amkit\\<profile> (not redirected by OneDrive
Known Folder Move). Resolution order: --data-dir / --profile flag, then AMKIT_PROFILE, then 'synthetic'.
AMKIT_DATA_ROOT overrides the root (used by tests).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from amkit.errors import PreconditionFailed

DEFAULT_PROFILE = "synthetic"
PROFILE_RE = re.compile(r"^(synthetic|real|eval-[a-z0-9]+|test-[a-z0-9-]+)$")

SUBDIRS = (
    "inbox",
    "inbox/processed",
    "inbox/rejected",
    "runs",
    "out",
    "backups",
    "logs",
    "config",
    "config/mappings",
    "config/templates",
    "secret",
    "jobs",
)


def repo_root() -> Path:
    """Repo root: AMKIT_REPO_ROOT, else the directory holding this package's pyproject.toml."""
    env = os.environ.get("AMKIT_REPO_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "amkit").is_dir():
            return parent
    raise PreconditionFailed("Cannot locate the amkit repo root; set AMKIT_REPO_ROOT.")


def data_root() -> Path:
    env = os.environ.get("AMKIT_DATA_ROOT")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "amkit"
    return Path.home() / ".local" / "share" / "amkit"


def resolve_profile(flag: str | None = None) -> str:
    profile = flag or os.environ.get("AMKIT_PROFILE") or DEFAULT_PROFILE
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
        return self.data_dir / "amkit.db"

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

    def ensure(self) -> None:
        for sub in SUBDIRS:
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)
        if self.data_class == "synthetic":
            self.ground_truth.mkdir(parents=True, exist_ok=True)


def get_paths(profile: str | None = None, data_dir: str | Path | None = None) -> Paths:
    prof = resolve_profile(profile)
    ddir = Path(data_dir) if data_dir else data_root() / prof
    return Paths(profile=prof, data_dir=ddir)


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
