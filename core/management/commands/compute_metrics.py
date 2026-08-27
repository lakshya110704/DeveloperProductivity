"""
Metric computation command for Developer Productivity.

Metrics are computed over a rolling time window and emitted as snapshots.
The JSONL fallback path aggregates with pandas; the MongoDB path aggregates
with the native pipeline.

Metric categories:
- FLOW METRICS: PR cycle time, review latency, review count, approval rate
- THROUGHPUT METRICS: PR merged/open/closed counts
- SIZE METRICS: PR additions, deletions, changed files
- ACTIVITY METRICS: commit counts, active days, commit velocity

MongoDB is optional. JSONL files are the source of truth during development.
"""
import os
import json
from datetime import datetime, timedelta, timezone
from django.core.management.base import BaseCommand
from django.conf import settings
from pathlib import Path
from pymongo import MongoClient
from statistics import mean
import pandas as pd

BASE_DIR = Path(settings.BASE_DIR)
FALLBACK_DIR = BASE_DIR / "data"
PULLS_FALLBACK = FALLBACK_DIR / "pull_requests.jsonl"
COMMITS_FALLBACK = FALLBACK_DIR / "commits.jsonl"
METRICS_FALLBACK = FALLBACK_DIR / "metric_snapshots.jsonl"

def try_get_db():
    uri = getattr(settings, "MONGO_URI", None)
    if not uri:
        raise RuntimeError("MONGO_URI not configured")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    client.admin.command("ping")
    db = client.get_default_database()
    if db is None:
        db = client["devprod"]
    return db

# -----------------------------
# Time parsing utilities
# -----------------------------
def parse_iso(s):
    if not s:
        return None
    if isinstance(s, datetime):
        dt = s
    else:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            try:
                dt = datetime.fromisoformat(s.split(".")[0])
            except Exception:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

# -----------------------------
# Fallback readers -> DataFrames
# -----------------------------
def _read_jsonl(path):
    if not path.exists():
        return []
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items

def read_pulls_df():
    rows = _read_jsonl(PULLS_FALLBACK)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["created_at"] = df.get("created_at", pd.Series(dtype=object)).map(parse_iso)
    df["merged_at"] = df.get("merged_at", pd.Series(dtype=object)).map(parse_iso)
    df["closed_at"] = df.get("closed_at", pd.Series(dtype=object)).map(parse_iso)
    df["author_anon_id"] = df["author"].map(lambda a: (a or {}).get("anon_id") if isinstance(a, dict) else None)
    df["review_count"] = df["reviews"].map(lambda r: len(r) if isinstance(r, list) else 0)
    df["approved"] = df["reviews"].map(
        lambda r: any((rv or {}).get("state") == "APPROVED" for rv in r) if isinstance(r, list) else False
    )
    df["first_review_at"] = df["reviews"].map(_first_review_at)
    return df

def _first_review_at(reviews):
    if not isinstance(reviews, list) or not reviews:
        return None
    dts = [parse_iso(rv.get("submitted_at")) for rv in reviews if isinstance(rv, dict)]
    dts = [d for d in dts if d is not None]
    return min(dts) if dts else None

def read_commits_df():
    rows = _read_jsonl(COMMITS_FALLBACK)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["committed_at"] = df.get("committed_at", pd.Series(dtype=object)).map(parse_iso)
    df["author_anon_id"] = df["author"].map(lambda a: (a or {}).get("anon_id") if isinstance(a, dict) else None)
    df["message_len"] = df.get("message", "").fillna("").map(len)
    df["date"] = df["committed_at"].map(lambda d: d.date() if pd.notna(d) and d is not None else None)
    return df

# kept for callers that still want plain dict rows (e.g. tests)
def read_pull_fallback():
    return _read_jsonl(PULLS_FALLBACK)

def read_commits_fallback():
    items = _read_jsonl(COMMITS_FALLBACK)
    for doc in items:
        doc["committed_at"] = parse_iso(doc.get("committed_at"))
    return items

def _snapshot(metric, target_type, target_id, value, count, window_start, now):
    return {
        "metric": metric,
        "target_type": target_type,
        "target_id": target_id,
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
        "value": value,
        "count": count,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

class Command(BaseCommand):
    help = "Compute 15+ developer productivity metrics for the last N days, aggregated with pandas. Writes to Mongo if available, else to fallback JSONL."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Window in days to compute metrics for")

    def handle(self, *args, **options):
        days = options.get("days", 30)
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=days)

        # =============================
        # Primary path: MongoDB metrics
        # =============================
        try:
            db = try_get_db()
            pulls_coll = db.pull_requests
            snapshots_coll = db.metric_snapshots
            self.stdout.write(self.style.SUCCESS("Connected to MongoDB for aggregation."))

            pipeline = [
                {"$match": {"created_at": {"$gte": window_start}, "merged_at": {"$ne": None}}},
                {"$project": {
                    "repo_full_name": 1,
                    "created_at": 1,
                    "merged_at": 1,
                    "cycle_time_seconds": {"$divide": [{"$subtract": ["$merged_at", "$created_at"]}, 1000]}
                }},
                {"$group": {
                    "_id": "$repo_full_name",
                    "avg_cycle_time_sec": {"$avg": "$cycle_time_seconds"},
                    "count": {"$sum": 1}
                }}
            ]
            results = list(pulls_coll.aggregate(pipeline))
            for r in results:
                ct_doc = _snapshot("pr_cycle_time_seconds", "repo", r["_id"], r.get("avg_cycle_time_sec"), r.get("count"), window_start, now)
                snapshots_coll.update_one(
                    {"metric": ct_doc["metric"], "target_type": ct_doc["target_type"], "target_id": ct_doc["target_id"], "window_start": ct_doc["window_start"], "window_end": ct_doc["window_end"]},
                    {"$set": ct_doc}, upsert=True,
                )
                count_doc = _snapshot("pr_merged_count", "repo", r["_id"], r.get("count"), r.get("count"), window_start, now)
                snapshots_coll.update_one(
                    {"metric": count_doc["metric"], "target_type": count_doc["target_type"], "target_id": count_doc["target_id"], "window_start": count_doc["window_start"], "window_end": count_doc["window_end"]},
                    {"$set": count_doc}, upsert=True,
                )
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(results)*2} metric snapshots to Mongo."))
            return
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not use Mongo: {e}"))
            self.stdout.write(self.style.WARNING("Falling back to pandas/JSONL aggregation."))

        # =============================
        # Fallback path: pandas aggregation over JSONL
        # =============================
        snapshots = []
        pulls = read_pulls_df()

        if not pulls.empty:
            merged = pulls[pulls["merged_at"].notna() & pulls["created_at"].notna()].copy()
            merged = merged[(merged["created_at"] >= window_start) & (merged["merged_at"] >= window_start)]
            merged["cycle_time_seconds"] = (merged["merged_at"] - merged["created_at"]).dt.total_seconds()

            frv = merged[merged["first_review_at"].notna() & (merged["first_review_at"] >= window_start)].copy()
            frv["first_review_seconds"] = (frv["first_review_at"] - frv["created_at"]).dt.total_seconds()
            frv = frv[frv["first_review_seconds"] >= 0]

            # ---- per-repo metrics ----
            by_repo = merged.groupby("repo_full_name")
            for repo, g in by_repo:
                snapshots.append(_snapshot("pr_cycle_time_seconds", "repo", repo, g["cycle_time_seconds"].mean(), len(g), window_start, now))
                snapshots.append(_snapshot("pr_merged_count", "repo", repo, len(g), len(g), window_start, now))
                snapshots.append(_snapshot("pr_avg_additions", "repo", repo, g["additions"].dropna().mean() if "additions" in g else None, g["additions"].dropna().shape[0] if "additions" in g else 0, window_start, now))
                snapshots.append(_snapshot("pr_avg_deletions", "repo", repo, g["deletions"].dropna().mean() if "deletions" in g else None, g["deletions"].dropna().shape[0] if "deletions" in g else 0, window_start, now))
                snapshots.append(_snapshot("pr_avg_changed_files", "repo", repo, g["changed_files"].dropna().mean() if "changed_files" in g else None, g["changed_files"].dropna().shape[0] if "changed_files" in g else 0, window_start, now))
                snapshots.append(_snapshot("pr_avg_review_count", "repo", repo, g["review_count"].mean(), len(g), window_start, now))
                snapshots.append(_snapshot("pr_approval_rate", "repo", repo, float(g["approved"].mean()), len(g), window_start, now))

            for repo, g in frv.groupby("repo_full_name"):
                snapshots.append(_snapshot("pr_first_review_seconds", "repo", repo, g["first_review_seconds"].mean(), len(g), window_start, now))

            # PRs opened in-window regardless of merge state, for open/closed throughput
            opened = pulls[pulls["created_at"].notna() & (pulls["created_at"] >= window_start)].copy()
            open_now = opened[opened["state"] == "open"]
            for repo, g in open_now.groupby("repo_full_name"):
                snapshots.append(_snapshot("pr_open_count", "repo", repo, len(g), len(g), window_start, now))

            closed_unmerged = opened[(opened["state"] == "closed") & opened["merged_at"].isna()]
            for repo, g in closed_unmerged.groupby("repo_full_name"):
                snapshots.append(_snapshot("pr_closed_without_merge_count", "repo", repo, len(g), len(g), window_start, now))

            # ---- per-developer (author) metrics ----
            for author, g in merged.groupby("author_anon_id"):
                if not author:
                    continue
                snapshots.append(_snapshot("pr_cycle_time_seconds_by_author", "developer", author, g["cycle_time_seconds"].mean(), len(g), window_start, now))

            for author, g in opened.groupby("author_anon_id"):
                if not author:
                    continue
                snapshots.append(_snapshot("pr_opened_count_by_author", "developer", author, len(g), len(g), window_start, now))

        # ---- commit-based metrics ----
        commits = read_commits_df()
        if not commits.empty:
            wc = commits[commits["committed_at"].notna() & (commits["committed_at"] >= window_start)].copy()

            for anon_id, g in wc.groupby("author_anon_id"):
                if not anon_id:
                    continue
                snapshots.append(_snapshot("commit_count_per_developer", "developer", anon_id, len(g), len(g), window_start, now))
                snapshots.append(_snapshot("commit_active_days_per_developer", "developer", anon_id, g["date"].nunique(), len(g), window_start, now))
                snapshots.append(_snapshot("commit_avg_message_length", "developer", anon_id, g["message_len"].mean(), len(g), window_start, now))

            for repo, g in wc.groupby("repo_full_name"):
                span_days = max((now - window_start).days, 1)
                snapshots.append(_snapshot("repo_commit_velocity", "repo", repo, len(g) / span_days, len(g), window_start, now))

        if snapshots:
            with open(METRICS_FALLBACK, "a", encoding="utf-8") as f:
                for s in snapshots:
                    f.write(json.dumps(s, default=str) + "\n")

        metric_kinds = sorted({s["metric"] for s in snapshots})
        self.stdout.write(self.style.SUCCESS(
            f"Computed {len(snapshots)} snapshot(s) across {len(metric_kinds)} metric types and wrote to {METRICS_FALLBACK}"
        ))
        self.stdout.write(self.style.SUCCESS(f"Metric types: {', '.join(metric_kinds)}"))
