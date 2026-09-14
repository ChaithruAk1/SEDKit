"""Per-batch symptom vocabulary (in/vocab_batch_NNNN.txt): approved symptom keys of the batch's applications.

Agents must reuse an approved key before inventing a new one, so recurring symptoms keep one name across runs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

TOP_KEYS_PER_APP = 30
NO_KEYS_LINE = "# no approved symptom keys yet"


def approved_keys(conn: sqlite3.Connection, app_ids: Iterable[str]) -> dict[str, list[tuple[str, int, str]]]:
    """App name -> [(symptom_key, tickets, most common category[/subcategory])], top keys first."""
    ids = sorted({a for a in app_ids if a})
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT a.name AS app, l.symptom_key AS key, COUNT(DISTINCT l.ticket_id) AS n, "
        "l.am_category AS category, l.am_subcategory AS subcategory "
        "FROM ai_ticket_label l JOIN ai_run r ON r.run_id = l.run_id AND r.status = 'approved' "
        "JOIN ticket t ON t.ticket_id = l.ticket_id JOIN application a ON a.app_id = t.app_id "
        f"WHERE t.app_id IN ({marks}) AND l.symptom_key IS NOT NULL AND l.symptom_key <> '' "
        "GROUP BY a.name, l.symptom_key, l.am_category, l.am_subcategory",
        ids,
    ).fetchall()
    per_key: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        gloss = r["category"] + (f"/{r['subcategory']}" if r["subcategory"] else "")
        per_key.setdefault((r["app"], r["key"]), {})[gloss] = int(r["n"])
    out: dict[str, list[tuple[str, int, str]]] = {}
    for (app, key), glosses in per_key.items():
        total = sum(glosses.values())
        top_gloss = sorted(glosses.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out.setdefault(app, []).append((key, total, top_gloss))
    return {app: sorted(keys, key=lambda k: (-k[1], k[0]))[:TOP_KEYS_PER_APP] for app, keys in sorted(out.items())}


def render_vocab(batch: str, keys: dict[str, list[tuple[str, int, str]]]) -> str:
    lines = [
        f"# Approved symptom keys for the applications in {batch} (key | tickets | usual category)",
        "# Reuse one of these keys when it names the same symptom; invent a new snake_case key only otherwise.",
    ]
    if not any(keys.values()):
        lines.append(NO_KEYS_LINE)
    for app, entries in keys.items():
        lines.append(f"## {app}")
        lines += [f"{key} | {n} | {gloss}" for key, n, gloss in entries]
    return "\n".join(lines) + "\n"
