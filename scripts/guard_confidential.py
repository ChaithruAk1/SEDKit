"""Block confidential material from entering git.

Modes:
  guard_confidential.py FILE...      check the given files (pre-commit passes staged files)
  guard_confidential.py --all        check every tracked file (CI)
  guard_confidential.py --history    scan the full git history, commit messages and identities
                                     (run before the first corporate push)

Checks: data-file extensions outside synthetic fixture paths, files over 1 MB, corporate-domain email addresses
and hostnames, real ITSM/Atlassian/SharePoint tenant hostnames, and terms from an external denylist of real
app/vendor/instance names kept outside the repo (%LOCALAPPDATA%\\amkit\\guard\\denylist.txt or AMKIT_GUARD_DENYLIST).
Content checks decode UTF-8, UTF-16 and UTF-32 (with or without BOM), so Windows "Unicode" exports are scanned.
"""

from __future__ import annotations

import argparse
import codecs
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIZE_LIMIT = 1_000_000
SIZE_EXEMPT = {"uv.lock", "web/package-lock.json"}

DATA_EXTENSIONS = {
    ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".bak", ".xlsx", ".xlsm", ".xlsb", ".xls", ".ods", ".csv",
    ".tsv", ".pptx", ".potx", ".ppt", ".odp", ".docx", ".doc", ".odt", ".pdf", ".msg", ".eml", ".pst", ".parquet",
    ".iqy", ".jsonl", ".zip", ".7z", ".rar", ".gz", ".tgz", ".tar",
}  # fmt: skip
ALLOWED_DATA_GLOBS = (
    "tests/fixtures/synthetic/*",
    ".claude/skills/*/examples.synthetic.jsonl",
)

# Built at runtime so this file never contains the literal domains or hostnames it looks for.
_DOMAIN_PARTS = [("se", "com"), ("schneider" + "-electric", "com"), ("schneider" + "-electric", "fr"), ("apc", "com")]
_CORP_DOMAINS = "|".join(re.escape(f"{a}.{b}") for a, b in _DOMAIN_PARTS)
# Anchored at '@' / a hostname label (no unbounded prefix) so large inputs cannot trigger quadratic backtracking.
CORP_EMAIL_RE = re.compile(r"@(?:[A-Za-z0-9\-]{1,63}\.){0,6}(?:" + _CORP_DOMAINS + r")\b", re.IGNORECASE)
CORP_HOST_RE = re.compile(
    r"(?:https?://|\b)(?:[A-Za-z0-9][A-Za-z0-9\-]{0,62}\.){1,6}(?:" + _CORP_DOMAINS + r")\b", re.IGNORECASE
)
_TENANT_SUFFIXES = ["service" + "-now.com", "atlassian" + ".net", "share" + "point.com"]
TENANT_RE = re.compile(
    r"\b([a-z0-9][a-z0-9\-]{0,62})\.(?:" + "|".join(re.escape(s) for s in _TENANT_SUFFIXES) + r")\b", re.IGNORECASE
)
ALLOWED_TENANTS = {"example", "contoso", "fabrikam", "yourinstance", "your-instance", "instance", "acme"}


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


# ---------------------------------------------------------------------------
# denylist
# ---------------------------------------------------------------------------


def denylist_path() -> Path:
    env = os.environ.get("AMKIT_GUARD_DENYLIST")
    if env:
        return Path(env)
    root = os.environ.get("AMKIT_DATA_ROOT") or (
        str(Path(os.environ["LOCALAPPDATA"]) / "amkit") if os.environ.get("LOCALAPPDATA") else ""
    )
    return Path(root) / "guard" / "denylist.txt" if root else Path("__no_denylist__")


def decode_text(raw: bytes) -> str | None:
    """Decode bytes as text (UTF-8/16/32 with or without BOM). Returns None for genuinely binary content."""
    for bom, codec in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if raw.startswith(bom):
            return raw.decode(codec, errors="ignore")
    head = raw[:8192]
    if b"\x00" not in head:
        return raw.decode("utf-8", errors="ignore")
    # BOM-less UTF-16: NULs at every other byte for ASCII-heavy text.
    even_nuls = head[1::2].count(0)
    odd_nuls = head[0::2].count(0)
    half = max(1, len(head) // 2)
    if even_nuls / half > 0.3 and odd_nuls / half < 0.05:
        return raw.decode("utf-16-le", errors="ignore")
    if odd_nuls / half > 0.3 and even_nuls / half < 0.05:
        return raw.decode("utf-16-be", errors="ignore")
    return None


def load_denylist(path: Path | None = None) -> list[re.Pattern[str]]:
    p = path or denylist_path()
    if not p.is_file():
        return []
    text = decode_text(p.read_bytes()) or ""
    terms = []
    for line in text.splitlines():
        term = line.strip().lstrip("﻿")
        if term and not term.startswith("#"):
            # Letter-only boundaries: OTHERVENDOR_L2, u_othervendor_ci and othervendor2026.csv all match.
            terms.append(re.compile(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", re.IGNORECASE))
    return terms


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def _data_allowed(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, g) or rel.startswith(g.rstrip("*")) for g in ALLOWED_DATA_GLOBS)


def _data_extension(rel: str) -> str | None:
    lower = rel.lower()
    for ext in sorted(DATA_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(ext):
            return ext
    return None


def check_name(rel: str, denylist: list[re.Pattern[str]] | None = None) -> list[Violation]:
    rel = rel.replace("\\", "/")
    out = []
    ext = _data_extension(rel)
    if ext and not _data_allowed(rel):
        out.append(Violation(rel, f"data file type '{ext}' outside synthetic fixture paths"))
    for pattern in denylist or []:
        if pattern.search(rel):
            out.append(Violation(rel, "file path matches a denylisted term"))
            break
    return out


def check_text(rel: str, text: str, denylist: list[re.Pattern[str]]) -> list[Violation]:
    out = []
    if "@" in text and CORP_EMAIL_RE.search(text):
        out.append(Violation(rel, "corporate-domain email address"))
    if CORP_HOST_RE.search(text):
        out.append(Violation(rel, "corporate-domain hostname or URL"))
    for tm in TENANT_RE.finditer(text):
        if tm.group(1).lower() not in ALLOWED_TENANTS:
            out.append(Violation(rel, f"real-looking tenant hostname '{tm.group(0)}'"))
            break
    for pattern in denylist:
        if pattern.search(text):
            out.append(Violation(rel, "matches a denylisted term (see external denylist)"))
            break
    return out


def check_bytes(rel: str, raw: bytes, denylist: list[re.Pattern[str]]) -> list[Violation]:
    violations: list[Violation] = []
    if len(raw) > SIZE_LIMIT and rel not in SIZE_EXEMPT:
        violations.append(Violation(rel, f"file larger than {SIZE_LIMIT // 1_000_000} MB"))
    text = decode_text(raw)
    if text is not None:
        violations += check_text(rel, text, denylist)
    return violations


def check_file(path: Path, denylist: list[re.Pattern[str]]) -> list[Violation]:
    rel = _rel(path)
    violations = check_name(rel, denylist)
    if not path.is_file():
        return violations
    return violations + check_bytes(rel, path.read_bytes(), denylist)


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=off", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
        ).stdout.decode("utf-8", errors="replace")
        return [REPO / p for p in out.split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache", ".ruff_cache"}
        files = []
        for root, dirs, names in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in skip]
            files += [Path(root) / n for n in names]
        return files


def _git(args: list[str], cwd: Path) -> bytes:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd, capture_output=True, check=True).stdout


def scan_history(denylist: list[re.Pattern[str]], repo: Path = REPO) -> list[Violation]:
    """Scan every blob reachable from any ref (incl. merge resolutions), commit messages and identities."""
    violations: list[Violation] = []
    objects = _git(["rev-list", "--all", "--objects"], repo).decode("utf-8", errors="replace")
    names: dict[str, list[str]] = {}
    for line in objects.splitlines():
        sha, _, name = line.partition(" ")
        if name:
            names.setdefault(sha, []).append(name)
    if names:
        batch = subprocess.run(
            ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input="\n".join(names).encode("ascii"),
            cwd=repo,
            capture_output=True,
            check=True,
        ).stdout.decode("ascii", errors="replace")
        for line in batch.splitlines():
            sha, _, kind = line.partition(" ")
            if kind.strip() != "blob":
                continue
            paths = names.get(sha, [])
            for name in paths:
                violations += [Violation(f"history:{v.path}", v.reason) for v in check_name(name, denylist)]
            raw = _git(["cat-file", "blob", sha], repo)
            label = paths[0] if paths else sha
            violations += [Violation(f"history:{v.path}", v.reason) for v in check_bytes(label, raw, denylist)]

    log = _git(["log", "--all", "--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x1e"], repo).decode(
        "utf-8", errors="replace"
    )
    for record in filter(None, (r.strip() for r in log.split("\x1e"))):
        sha, author, committer, message = [*record.split("\x00"), "", "", ""][:4]
        label = f"history:commit {sha[:10]}"
        violations += [
            Violation(label, v.reason) for v in check_text(label, f"{author}\n{committer}\n{message}", denylist)
        ]
    # De-duplicate identical (path, reason) pairs.
    return list(dict.fromkeys(violations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--all", action="store_true", help="check all tracked files")
    parser.add_argument("--history", action="store_true", help="scan full git history")
    args = parser.parse_args(argv)

    denylist = load_denylist()
    if not denylist:
        print(f"guard: no denylist loaded from {denylist_path()} (real-name check disabled)", file=sys.stderr)
    violations: list[Violation] = []
    if args.history:
        violations += scan_history(denylist)
    files = tracked_files() if args.all else [Path(f) for f in args.files]
    for f in files:
        violations += check_file(f if f.is_absolute() else REPO / f, denylist)

    if violations:
        print("Confidential-material guard FAILED:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print("Real data belongs in DATA_DIR (%LOCALAPPDATA%\\amkit\\<profile>), never in git.", file=sys.stderr)
        return 1
    if not denylist and os.environ.get("AMKIT_GUARD_REQUIRE_DENYLIST") == "1":
        print("Denylist required but not found at " + str(denylist_path()), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
