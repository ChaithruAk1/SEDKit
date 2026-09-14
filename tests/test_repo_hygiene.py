from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from sed import claude_setup
from sed.doctor import skill_name_problems
from sed.settings import load_agent_config
from tests.conftest import REPO, load_script

guard = load_script("guard_confidential")
CORP = "se" + ".com"
TENANT = "acmecorp" + ".service-now.com"


def test_repo_passes_guard():
    denylist = guard.load_denylist()
    violations = []
    for f in guard.tracked_files():
        violations += guard.check_file(f, denylist)
    assert not violations, "\n".join(map(str, violations))


@pytest.mark.parametrize(
    ("rel", "blocked"),
    [
        ("exports/incident_2026-08.xlsx", True),
        ("exports/INCIDENT_2026-08.XLSX", True),
        ("exports\\costs.xlsb", True),
        ("sed.db", True),
        ("notes/tickets.csv", True),
        ("templates/corporate.potx", True),
        ("bundle.7z", True),
        ("costs.ods", True),
        ("tests/fixtures/synthetic/incident_small.csv", False),
        (".claude/skills/sed-triage-batch/examples.synthetic.jsonl", False),
        ("src/sed/db.py", False),
    ],
)
def test_guard_extension_rules(rel: str, blocked: bool):
    assert bool(guard.check_name(rel)) is blocked


def test_guard_detects_corporate_email_host_and_tenant():
    assert guard.check_text("x.md", f"contact jane.doe@{CORP} for access", [])
    assert guard.check_text("x.md", f"see https://jira.{CORP}/browse/ABC-1", [])
    assert not guard.check_text("x.md", "contact someone@example.com at https://base.com", [])
    assert guard.check_text("x.md", f"https://{TENANT}/incident.do", [])
    assert not guard.check_text("x.md", "https://example.service-now.com/incident.do", [])


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-32"])
def test_guard_scans_utf16_and_utf32_text(tmp_path: Path, encoding: str):
    f = tmp_path / "export.txt"
    f.write_bytes(f"Caller: jane@{CORP}\r\nState: New\r\n".encode(encoding))
    assert any("email" in v.reason for v in guard.check_file(f, []))


def test_guard_denylist_bom_underscores_and_paths(tmp_path: Path):
    deny = tmp_path / "denylist.txt"
    deny.write_bytes(b"\xef\xbb\xbfOrionTopSecretApp\r\n# comment\r\nOthervendorx\r\n")
    patterns = guard.load_denylist(deny)
    assert guard.check_text("x.md", "Incident on oriontopsecretapp today", patterns)
    assert guard.check_text("x.md", "group OTHERVENDORX_L2 and u_othervendorx_ci", patterns)
    assert not guard.check_text("x.md", "Incident on Orion ERP", patterns)
    assert guard.check_name("docs/othervendorx2026-notes.md", patterns)
    deny.write_text("OrionTopSecretApp\n", encoding="utf-16")
    assert guard.check_text("x.md", "OrionTopSecretApp", guard.load_denylist(deny))


def test_guard_blocks_large_files(tmp_path: Path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (guard.SIZE_LIMIT + 1))
    assert any("larger" in v.reason for v in guard.check_file(big, []))


def test_guard_cli_exit_codes(tmp_path: Path):
    bad = tmp_path / "leak.xlsx"
    bad.write_bytes(b"PK\x03\x04")
    db_file = tmp_path / "leak.db"
    db_file.write_bytes(b"SQLite format 3\x00")
    email = tmp_path / "notes.md"
    email.write_text(f"ask jane@{CORP}\n", encoding="utf-8")
    good = tmp_path / "fine.py"
    good.write_text("print('ok')\n", encoding="utf-8")
    for leak in (bad, db_file, email):
        assert guard.main([str(leak)]) == 1, leak
    assert guard.main([str(good)]) == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_guard_history_scan_finds_hidden_leaks(tmp_path: Path):
    repo = tmp_path / "hist"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}  # fmt: skip

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    git("init", "-b", "main")
    (repo / "readme.md").write_text("hello\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "init")
    # Non-ASCII data file name, later deleted.
    (repo / "Coûts_2026.xlsx").write_bytes(b"PK\x03\x04")
    git("add", ".")
    git("commit", "-m", "costs")
    git("rm", "-q", "Coûts_2026.xlsx")
    git("commit", "-m", f"remove export from jane@{CORP}")
    # Evil merge: leak only exists in the merge resolution.
    git("checkout", "-q", "-b", "side")
    (repo / "side.md").write_text("side\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "side")
    git("checkout", "-q", "main")
    (repo / "main.md").write_text("main\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "main")
    git("merge", "--no-commit", "--no-ff", "side")
    (repo / "merged.md").write_text(f"https://{TENANT}\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "merge")

    reasons = [str(v) for v in guard.scan_history([], repo)]
    assert any("Coûts_2026.xlsx" in r and "data file" in r for r in reasons), reasons
    assert any("tenant" in r for r in reasons), reasons
    assert any("commit" in r and "email" in r for r in reasons), reasons


def test_precommit_config_runs_guard_on_all_file_types():
    cfg = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {h["id"]: h for repo in cfg["repos"] for h in repo["hooks"]}
    assert all(repo["repo"] == "local" for repo in cfg["repos"])
    guard_hook = hooks["guard-confidential"]
    assert "scripts/guard_confidential.py" in guard_hook["entry"]
    assert guard_hook.get("types", ["file"]) == ["file"]
    assert "exclude" not in guard_hook
    assert {"detect-secrets", "ruff-check", "ruff-format"} <= hooks.keys()


def test_claude_md_prefix_block_matches_repo_agent_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "no-machine-override"))
    assert claude_setup.prefix_block_in_sync(load_agent_config(), REPO / "CLAUDE.md")


def test_committed_settings_allow_both_shell_tools():
    import json

    data = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allow = data["permissions"]["allow"]
    assert "Bash(uv run sed:*)" in allow and "PowerShell(uv run sed:*)" in allow
    assert data["env"] == {"UV_NO_SYNC": "1", "PYTHONUTF8": "1"}
    # Review verdicts and run approval are human decisions: an ask rule overrides the blanket allow for agents.
    assert {"Bash(uv run sed review:*)", "PowerShell(uv run sed review:*)"} <= set(data["permissions"]["ask"])


def test_project_skills_prefixed_and_named():
    skills = REPO / ".claude" / "skills"
    if not skills.is_dir() or not any(p.is_dir() for p in skills.iterdir()):
        pytest.skip("no project skills yet")
    _clashes, bad = skill_name_problems(REPO)
    assert not bad, bad


def test_skill_name_problems_detects_bad_frontmatter(tmp_path: Path):
    (tmp_path / ".claude" / "skills" / "sed-good").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "sed-good" / "SKILL.md").write_text("---\nname: sed-good\n---\n", "utf-8")
    (tmp_path / ".claude" / "skills" / "sed-bad").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "sed-bad" / "SKILL.md").write_text("---\nname: other\n---\n", "utf-8")
    (tmp_path / ".claude" / "skills" / "review").mkdir(parents=True)
    _, bad = skill_name_problems(tmp_path)
    assert any(b.startswith("sed-bad") for b in bad)
    assert any(b.startswith("review") for b in bad)
    assert not any(b.startswith("sed-good") for b in bad)


def test_gitignore_covers_data_and_local_settings():
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    for entry in ("*.db", ".claude/settings.local.json", ".env", "web/node_modules/"):
        assert entry in ignore


# Zero-width, bidi-control, word-joiner, soft-hyphen and mid-file BOM characters: invisible in review, so they can hide
# changes (or break YAML keys / regexes). Built with chr() so this file stays clean itself.
_INVISIBLE = {chr(c) for c in (0x00AD, *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2065), 0xFEFF)}
_TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".json", ".toml", ".ps1", ".sql", ".js", ".mjs", ".ts", ".tsx", ".txt"}


def test_no_invisible_characters_in_source():
    offenders = []
    for top in ("src", "tests", "config", "scripts", ".claude", "docs", "templates", "evals"):
        base = REPO / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                bad = sorted({f"U+{ord(ch):04X}" for ch in line if ch in _INVISIBLE})
                if bad:
                    offenders.append(f"{path.relative_to(REPO)}:{lineno} {bad}")
    for name in ("CLAUDE.md", "README.md", "pyproject.toml"):
        text = (REPO / name).read_text(encoding="utf-8")
        if any(ch in _INVISIBLE for ch in text):
            offenders.append(name)
    assert not offenders, "\n".join(offenders[:20])
