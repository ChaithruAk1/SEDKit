"""Recurring-issue text candidates: groups of similar incidents per application over months, without AI.

Per application, scrubbed short descriptions of incidents opened in the window are normalised (lower case, tokens with
digits and the application's own name removed), vectorised with TF-IDF (words and word pairs, English stop words) and
compared with chunked sparse cosine similarity. Near-duplicates (cosine >= STRONG, typo variants) always join; weaker
pairs at or above `threshold` join only as mutual `top_k` nearest neighbours, so bridging texts cannot chain symptoms.
Union-find groups with at least `min_size` incidents are candidates. Each group carries what a reviewer or the
sed-find-recurring skill needs to judge it: size, first and last day, weekly counts, a day-of-month histogram with a
periodicity verdict (`monthly`, `burst` on one day, `episode` that appeared inside the window and ended within weeks,
or `none`), burst days, the configuration items involved, changes on the same application in the 7 days before a group
that starts inside the window, problem links, top terms and a few sample texts.

Deterministic: the same data and parameters give the same groups in the same order. The group id is anchored on the
group's earliest incident (`tc:<app_id>:<ticket number>`), so it stays stable while a group grows.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np

from sed.calendar import iso_utc, local_midnight_utc, month_label, to_local, week_label

TOKEN_WITH_DIGIT = re.compile(r"\b\w*\d\w*\b")
NON_WORD = re.compile(r"[^\w]+")
CHUNK = 1500
SAMPLES = 5
CHANGE_LOOKBACK_DAYS = 7
ONSET_MARGIN_DAYS = 14
EPISODE_MAX_DAYS = 45
STRONG = 0.85  # near-duplicate texts (typos, numbers) always join


@dataclass
class CandidateGroup:
    group_id: str
    app_id: str
    app_name: str | None
    ticket_ids: list[str]
    first_day: str
    last_day: str
    weekly: dict[str, int]
    day_of_month: dict[int, int]
    months_active: int
    span_months: int
    periodic: bool
    periodicity: str  # monthly | burst | episode | none
    bursts: list[dict[str, Any]]
    cis: dict[str, int]
    changes_before_onset: list[dict[str, Any]]
    problems: list[str]
    terms: list[str]
    samples: list[str]
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.ticket_ids)


def normalise(text: str | None) -> str:
    lowered = TOKEN_WITH_DIGIT.sub(" ", (text or "").lower())
    return " ".join(NON_WORD.sub(" ", lowered).split())


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _pairs(matrix: Any, threshold: float, top_k: int) -> list[tuple[int, int]]:
    """(i, j), i < j, to join (rows L2-normalised): near-duplicates (cosine >= STRONG) always, and pairs at or above
    `threshold` only when each is among the other's top_k neighbours, so a few bridging texts cannot chain unrelated
    symptoms into one group while typo variants of one text stay together."""
    strong: set[tuple[int, int]] = set()
    directed: set[tuple[int, int]] = set()
    n = matrix.shape[0]
    for start in range(0, n, CHUNK):
        block = (matrix[start : start + CHUNK] @ matrix.T).toarray()
        for offset, row in enumerate(block):
            i = start + offset
            row[i] = 0.0
            strong.update((min(i, int(j)), max(i, int(j))) for j in np.flatnonzero(row >= STRONG))
            idx = np.argpartition(-row, top_k)[:top_k] if top_k < n else np.arange(n)
            directed.update((i, int(j)) for j in idx if row[j] >= threshold and i != j)
    mutual = {(min(i, j), max(i, j)) for i, j in directed if (j, i) in directed}
    return sorted(strong | mutual)


def _periodicity(days: list[date], span_months: int) -> tuple[bool, str, dict[int, int], int]:
    dom = Counter(d.day for d in days)
    months = {month_label(d) for d in days}
    best_window = max((sum(dom.get(((s + k - 1) % 31) + 1, 0) for k in range(3)) for s in range(1, 32)), default=0)
    concentration = best_window / len(days) if days else 0.0
    monthly = len(months) >= 4 and len(months) / max(1, span_months) >= 0.6 and concentration >= 0.6
    by_day = Counter(days)
    burst = bool(by_day) and max(by_day.values()) >= max(5, 0.5 * len(days))
    kind = "monthly" if monthly else "burst" if burst else "none"
    return monthly, kind, dict(sorted(dom.items())), len(months)


def text_candidates(
    conn: sqlite3.Connection,
    as_of: date,
    tz: str,
    *,
    months: int = 12,
    min_size: int = 5,
    threshold: float = 0.6,
    top_k: int = 10,
    app_ids: list[str] | None = None,
) -> list[CandidateGroup]:
    from sklearn.feature_extraction.text import TfidfVectorizer

    end = local_midnight_utc(as_of + timedelta(days=1), tz)
    start = local_midnight_utc(_months_back(as_of, months), tz)
    where = "t.kind = 'incident' AND t.app_id IS NOT NULL AND t.opened_at >= ? AND t.opened_at < ?"
    params: list[Any] = [iso_utc(start), iso_utc(end)]
    if app_ids:
        where += f" AND t.app_id IN ({', '.join('?' for _ in app_ids)})"
        params += app_ids
    rows = conn.execute(
        "SELECT t.ticket_id, t.number, t.app_id, a.name AS app_name, t.opened_at, t.short_description, t.cmdb_ci_raw, "
        f"t.problem_id FROM ticket t LEFT JOIN application a ON a.app_id = t.app_id WHERE {where} "
        "ORDER BY t.app_id, t.opened_at, t.ticket_id",
        params,
    ).fetchall()
    by_app: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_app[r["app_id"]].append(r)
    groups: list[CandidateGroup] = []
    for app_id in sorted(by_app):
        items = by_app[app_id]
        app_words = set(normalise(items[0]["app_name"]).split())  # the app name says nothing about the symptom
        texts = [" ".join(w for w in normalise(r["short_description"]).split() if w not in app_words) for r in items]
        keep = [i for i, t in enumerate(texts) if t]
        if len(keep) < min_size:
            continue
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words="english")
        try:
            matrix = vectorizer.fit_transform([texts[i] for i in keep])
        except ValueError:  # empty vocabulary
            continue
        uf = _UnionFind(len(keep))
        for i, j in _pairs(matrix, threshold, top_k):
            uf.union(i, j)
        members: dict[int, list[int]] = defaultdict(list)
        for i in range(len(keep)):
            members[uf.find(i)].append(i)
        vocab = np.array(vectorizer.get_feature_names_out())
        for local in members.values():
            if len(local) < min_size:
                continue
            chosen = [items[keep[i]] for i in sorted(local)]
            centroid = np.asarray(matrix[sorted(local)].mean(axis=0)).ravel()
            terms = [str(vocab[i]) for i in np.argsort(-centroid, kind="stable")[:5] if centroid[i] > 0]
            groups.append(_group(conn, app_id, chosen, terms, _months_back(as_of, months), months, tz))
    groups.sort(key=lambda g: (-g.size, g.group_id))
    return groups


def _months_back(day: date, months: int) -> date:
    y, m = day.year, day.month - months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _group(
    conn: sqlite3.Connection,
    app_id: str,
    rows: list[sqlite3.Row],
    terms: list[str],
    window_start: date,
    months: int,
    tz: str,
) -> CandidateGroup:
    days = [to_local(r["opened_at"], tz).date() for r in rows]
    first, last = min(days), max(days)
    periodic, kind, dom, months_active = _periodicity(days, months)
    starts_in_window = first > window_start + timedelta(days=ONSET_MARGIN_DAYS)
    if kind == "none" and starts_in_window and (last - first).days <= EPISODE_MAX_DAYS:
        kind = "episode"  # appeared inside the window and stopped within weeks, e.g. after a change
    by_day = Counter(days)
    threshold = max(5, int(np.median(list(by_day.values())) * 3))
    bursts = [{"day": d.isoformat(), "tickets": n} for d, n in sorted(by_day.items(), key=lambda kv: (-kv[1], kv[0]))]
    bursts = [b for b in bursts if b["tickets"] >= threshold][:3]
    changes: list[sqlite3.Row] = []
    # Only a group that starts inside the window has an onset; one present from the start has none to explain.
    if starts_in_window:
        onset_utc = local_midnight_utc(first + timedelta(days=1), tz)
        lo, hi = iso_utc(onset_utc - timedelta(days=CHANGE_LOOKBACK_DAYS + 1)), iso_utc(onset_utc)
        # A change counts when it was raised, started or ended in the lookback: exports often carry planned dates.
        changes = conn.execute(
            "SELECT number, close_code, CASE WHEN start_date >= :lo AND start_date < :hi THEN start_date "
            "WHEN end_date >= :lo AND end_date < :hi THEN end_date ELSE opened_at END AS at FROM ticket "
            "WHERE kind = 'change_request' AND app_id = :app AND ((opened_at >= :lo AND opened_at < :hi) "
            "OR (start_date >= :lo AND start_date < :hi) OR (end_date >= :lo AND end_date < :hi)) "
            "ORDER BY at, number",
            {"app": app_id, "lo": lo, "hi": hi},
        ).fetchall()
    anchor = min(rows, key=lambda r: (r["opened_at"], r["ticket_id"]))
    samples = []
    for r in rows:
        text = " ".join((r["short_description"] or "").split())
        if text and text not in samples:
            samples.append(text[:160])
        if len(samples) >= SAMPLES:
            break
    group_id = f"tc:{app_id}:{anchor['number']}"
    weekly = dict(sorted(Counter(week_label(d) for d in days).items()))
    return CandidateGroup(
        group_id=group_id,
        app_id=app_id,
        app_name=anchor["app_name"],
        ticket_ids=sorted(r["ticket_id"] for r in rows),
        first_day=first.isoformat(),
        last_day=last.isoformat(),
        weekly=weekly,
        day_of_month=dom,
        months_active=months_active,
        span_months=months,
        periodic=periodic,
        periodicity=kind,
        bursts=bursts,
        cis=dict(Counter(r["cmdb_ci_raw"] for r in rows if r["cmdb_ci_raw"]).most_common(5)),
        changes_before_onset=[
            {"number": c["number"], "close_code": c["close_code"], "at": c["at"][:10]} for c in changes
        ],
        problems=sorted({r["problem_id"] for r in rows if r["problem_id"]}),
        terms=terms,
        samples=samples,
        facts={
            "tickets": len(rows),
            "first_day": first.isoformat(),
            "last_day": last.isoformat(),
            "months_active": months_active,
            "max_tickets_per_day": max(by_day.values()),
        },
    )


def as_dict(group: CandidateGroup) -> dict[str, Any]:
    return {
        "group_id": group.group_id,
        "app_id": group.app_id,
        "app": group.app_name,
        "tickets": group.size,
        "first_day": group.first_day,
        "last_day": group.last_day,
        "periodicity": group.periodicity,
        "months_active": group.months_active,
        "day_of_month": group.day_of_month,
        "bursts": group.bursts,
        "weekly": group.weekly,
        "cis": group.cis,
        "changes_before_onset": group.changes_before_onset,
        "problems": group.problems,
        "terms": group.terms,
        "samples": group.samples,
    }
