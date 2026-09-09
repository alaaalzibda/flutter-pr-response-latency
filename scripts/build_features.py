"""Build the paper's maintainer-side features from raw Flutter PR records.

The identity of a contributor is supplied by a mapping, so the same code can build
the baseline (real usernames) and the perturbed variants (usernames split across
artificial accounts) without any other difference between them.

Feature definitions follow measure_features_maintainers.py from the authors'
replication package: history features are computed only from a contributor's
PRIOR pull requests, and fall back to 0 when the contributor has no history.
"""
from __future__ import annotations

import json
import pathlib
import statistics
from datetime import datetime, timedelta

DAY = 24.0
WEEK = 24.0 * 7
# Service accounts that act like maintainers but are not people.
BOTS = {"auto-submit", "fluttergithubbot", "flutter-dashboard"}


def ts(s):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def is_bot(actor):
    if not actor:
        return True
    if actor.get("__typename") == "Bot":
        return True
    login = str(actor.get("login", ""))
    return login.endswith("[bot]") or login in BOTS


def maintainer_set(actors_path, prs_path, min_reviews=3):
    """Identify accounts with write access.

    Primary signal: merging or closing a pull request, which requires write access.
    Secondary signal: reviewing at least `min_reviews` distinct PRs written by
    somebody else, which catches reviewers on repositories where a bot performs
    the actual merge (Flutter's auto-submit does exactly this).
    """
    priv = set()
    for line in pathlib.Path(actors_path).read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        for key in ("merged_by", "closed_by"):
            login = d.get(key)
            if login and not is_bot({"login": login}):
                priv.add(login)

    reviewed: dict[str, int] = {}
    for line in pathlib.Path(prs_path).read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        author = d.get("author")
        if is_bot(author):
            continue
        seen = set()
        for n in (d.get("reviews") or {}).get("nodes") or []:
            a = n.get("author")
            if is_bot(a) or a["login"] == author["login"]:
                continue
            seen.add(a["login"])
        for login in seen:
            reviewed[login] = reviewed.get(login, 0) + 1

    return priv | {u for u, c in reviewed.items() if c >= min_reviews}


def load(path, maintainers):
    """Read raw records and reduce each to the timing facts we need."""
    rows = []
    for line in pathlib.Path(path).read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        author = d.get("author")
        if is_bot(author):
            continue
        login = author["login"]
        opened = ts(d["createdAt"])

        # Every human event on the PR, tagged with whether the actor has write access.
        events = []
        for group in ("comments", "reviews"):
            for n in (d.get(group) or {}).get("nodes") or []:
                a = n.get("author")
                if is_bot(a):
                    continue
                t = ts(n["createdAt"])
                if t is None or t <= opened:
                    continue
                events.append((t, a["login"], a["login"] in maintainers))
        events.sort()

        maint_at = next((t for t, who, priv in events if priv and who != login), None)
        contrib_at = None
        if maint_at is not None:
            contrib_at = next((t for t, who, _ in events if who == login and t > maint_at), None)

        rows.append(
            {
                "number": d["number"],
                "login": login,
                "opened_at": opened,
                "closed_at": ts(d.get("closedAt")),
                "merged_at": ts(d.get("mergedAt")),
                "maintainer_at": maint_at,
                "maintainer_latency": (maint_at - opened).total_seconds() / 3600 if maint_at else None,
                "contributor_latency": (contrib_at - maint_at).total_seconds() / 3600 if contrib_at else None,
                "pr_description": len((d.get("title") or "").split()) + len((d.get("bodyText") or "").split()),
                "pr_commits": (d.get("commits") or {}).get("totalCount", 0),
                "pr_changed_lines": (d.get("additions") or 0) + (d.get("deletions") or 0),
                "pr_changed_files": d.get("changedFiles") or 0,
                "events": events,
            }
        )
    rows.sort(key=lambda r: r["opened_at"])
    return rows


def latency_class(hours):
    if hours <= DAY:
        return 0
    if hours <= WEEK:
        return 1
    return 2


def build(rows, identity=None):
    """Compute per-PR features. `identity` maps PR number -> account name."""
    if identity is None:
        identity = {}

    hist: dict[str, list[dict]] = {}          # account -> its earlier PRs
    project: list[dict] = []                  # every earlier PR, for project-level features
    out = []

    for r in rows:
        who = identity.get(r["number"], r["login"])
        now = r["opened_at"]
        mine = hist.get(who, [])

        n_pulls = len(mine)
        n_open = sum(1 for p in mine if p["closed_at"] is None or p["closed_at"] >= now)
        merged = sum(1 for p in mine if p["merged_at"] is not None and p["merged_at"] < now)
        acceptance = merged / n_pulls if n_pulls else 0.0
        prior_lat = [p["contributor_latency"] for p in mine if p["contributor_latency"] is not None]
        median_lat = statistics.median(prior_lat) if prior_lat else 0.0

        cutoff = now - timedelta(days=90)
        recent = [p for p in project if p["opened_at"] >= cutoff]
        proj_open = sum(1 for p in project if p["closed_at"] is None or p["closed_at"] >= now)
        proj_lat = [
            p["maintainer_latency"]
            for p in recent
            if p["maintainer_at"] is not None and p["maintainer_at"] < now
        ]

        maintainers, community = set(), set()
        for p in recent:
            for t, actor, priv in p["events"]:
                if t < cutoff or t >= now:
                    continue
                (maintainers if priv else community).add(actor)

        if r["maintainer_latency"] is not None:
            out.append(
                {
                    "number": r["number"],
                    "opened_at": now,
                    "account": who,
                    "true_login": r["login"],
                    # PR features
                    "pr_hour": now.hour,
                    "pr_day": now.isoweekday(),
                    "pr_description": r["pr_description"],
                    "pr_commits": r["pr_commits"],
                    "pr_changed_lines": r["pr_changed_lines"],
                    "pr_changed_files": r["pr_changed_files"],
                    # contributor history features -- the ones identity errors corrupt
                    "contributor_pulls": n_pulls,
                    "contributor_open_pulls": n_open,
                    "contributor_acceptance_rate": acceptance,
                    "contributor_median_latency": median_lat,
                    # project features
                    "project_pulls": len(recent),
                    "project_open_pulls": proj_open,
                    "project_maintainers": len(maintainers),
                    "project_community": len(community),
                    "project_median_latency": statistics.median(proj_lat) if proj_lat else 0.0,
                    # target
                    "target": latency_class(r["maintainer_latency"]),
                    "maintainer_latency": r["maintainer_latency"],
                }
            )

        hist.setdefault(who, []).append(r)
        project.append(r)

    return out


FEATURES = [
    "pr_hour",
    "pr_day",
    "pr_description",
    "pr_commits",
    "pr_changed_lines",
    "pr_changed_files",
    "contributor_pulls",
    "contributor_open_pulls",
    "contributor_acceptance_rate",
    "contributor_median_latency",
    "project_pulls",
    "project_open_pulls",
    "project_maintainers",
    "project_community",
    "project_median_latency",
]

HISTORY_FEATURES = [
    "contributor_pulls",
    "contributor_open_pulls",
    "contributor_acceptance_rate",
    "contributor_median_latency",
]
