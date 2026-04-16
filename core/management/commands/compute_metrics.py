"""
Metric computation command for Developer Productivity.

Metrics are computed over a rolling time window and emitted as snapshots.

Metric categories:
- FLOW METRICS (primary): PR cycle time, review latency
- THROUGHPUT METRICS (supporting): PR merged count
- ACTIVITY METRICS (experimental): commit counts per developer

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

BASE_DIR = Path(settings.BASE_DIR)
FALLBACK_DIR = BASE_DIR / "data"
PULLS_FALLBACK = FALLBACK_DIR / "pull_requests.jsonl"
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
# Pull Request fallback readers
# -----------------------------
def read_pull_fallback():
    if not PULLS_FALLBACK.exists():
        return []
    items = []
    with open(PULLS_FALLBACK, "r", encoding="utf-8") as f:
        for line in f:
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items

# -----------------------------
# Commit fallback readers
# -----------------------------
def read_commits_fallback():
    """
    Read commit records from JSONL fallback.
    Returns a list of commit dicts.
    """
    path = Path(settings.BASE_DIR) / "data" / "commits.jsonl"
    if not path.exists():
        return []

    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                doc = json.loads(line)
                doc["committed_at"] = parse_iso(doc.get("committed_at"))
                items.append(doc)
            except Exception:
                continue
    return items

class Command(BaseCommand):
    help = "Compute metrics (pr_cycle_time_seconds and pr_merged_count) for last N days. Writes to Mongo if available, else to fallback JSONL."

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

            # FLOW METRIC: PR cycle time & throughput (Mongo)
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

            #PR Metrics
            results = list(pulls_coll.aggregate(pipeline))
            for r in results:
                # Metric: pr_cycle_time_seconds (delivery speed)
                ct_doc = {
                    "metric": "pr_cycle_time_seconds",
                    "target_type": "repo",
                    "target_id": r["_id"],
                    "window_start": window_start,
                    "window_end": now,
                    "value": r.get("avg_cycle_time_sec"),
                    "count": r.get("count"),
                    "computed_at": datetime.now(timezone.utc)
                }
                snapshots_coll.update_one(
                    {"metric": ct_doc["metric"], "target_type": ct_doc["target_type"], "target_id": ct_doc["target_id"], "window_start": ct_doc["window_start"], "window_end": ct_doc["window_end"]},
                    {"$set": ct_doc},
                    upsert=True
                )

                # Metric: pr_merged_count (throughput)
                count_doc = {
                    "metric": "pr_merged_count",
                    "target_type": "repo",
                    "target_id": r["_id"],
                    "window_start": window_start,
                    "window_end": now,
                    "value": r.get("count"),
                    "count": r.get("count"),
                    "computed_at": datetime.now(timezone.utc)
                }
                snapshots_coll.update_one(
                    {"metric": count_doc["metric"], "target_type": count_doc["target_type"], "target_id": count_doc["target_id"], "window_start": count_doc["window_start"], "window_end": count_doc["window_end"]},
                    {"$set": count_doc},
                    upsert=True
                )

            self.stdout.write(self.style.SUCCESS(f"Wrote {len(results)*2} metric snapshots to Mongo."))
            return

        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not use Mongo: {e}"))
            self.stdout.write(self.style.WARNING("Falling back to JSONL aggregation."))

        # =============================
        # Fallback path: JSONL metrics
        # =============================
        # FLOW METRICS (primary): Pull Request analytics
        pulls = read_pull_fallback()
        per_repo = {}

        for p in pulls:
            # parse created/merged
            c = parse_iso(p.get("created_at"))
            m = parse_iso(p.get("merged_at"))
            if not c or not m:
                continue
            if c < window_start or m < window_start:
                continue
            repo = p.get("repo_full_name", "unknown")
            delta = (m - c).total_seconds()
            per_repo.setdefault(repo, {"cycle_times": [], "first_review_seconds": [], "merged_count": 0})
            per_repo[repo]["cycle_times"].append(delta)
            per_repo[repo]["merged_count"] += 1

            # compute first review time if reviews exist
            reviews = p.get("reviews", []) or []
            first_review_dt = None
            for rv in reviews:
                sub = rv.get("submitted_at")
                sub_dt = parse_iso(sub)
                if sub_dt:
                    if first_review_dt is None or sub_dt < first_review_dt:
                        first_review_dt = sub_dt
            if first_review_dt:
                # only include when first review exists and within window
                if first_review_dt >= window_start:
                    fr_delta = (first_review_dt - c).total_seconds()
                    if fr_delta >= 0:
                        per_repo[repo]["first_review_seconds"].append(fr_delta)

        #Cycle Time Metrics
        snapshots = []
        for repo, vals in per_repo.items():
            # Metric: pr_cycle_time_seconds (delivery speed)
            deltas = vals.get("cycle_times", [])
            avg = mean(deltas) if deltas else None
            ct_snap = {
                "metric": "pr_cycle_time_seconds",
                "target_type": "repo",
                "target_id": repo,
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
                "value": avg,
                "count": len(deltas),
                "computed_at": datetime.now(timezone.utc).isoformat()
            }
            snapshots.append(ct_snap)

            # Metric: pr_merged_count (throughput)
            count_snap = {
                "metric": "pr_merged_count",
                "target_type": "repo",
                "target_id": repo,
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
                "value": vals.get("merged_count", 0),
                "count": vals.get("merged_count", 0),
                "computed_at": datetime.now(timezone.utc).isoformat()
            }
            snapshots.append(count_snap)

            # Metric: pr_first_review_seconds (review responsiveness)
            fr_list = vals.get("first_review_seconds", [])
            fr_avg = mean(fr_list) if fr_list else None
            fr_snap = {
                "metric": "pr_first_review_seconds",
                "target_type": "repo",
                "target_id": repo,
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
                "value": fr_avg,
                "count": len(fr_list),
                "computed_at": datetime.now(timezone.utc).isoformat()
            }
            snapshots.append(fr_snap)

        # FLOW PARTICIPATION METRIC (developer-level)
        # Metric: pr_cycle_time_seconds_by_author
        # Measures how PRs authored by a developer flow through the system.
        # NOTE: This is a flow signal, NOT a performance score.
        per_author = {}

        for p in pulls:
            author = (p.get("author") or {}).get("anon_id")
            if not author:
                continue

            c = parse_iso(p.get("created_at"))
            m = parse_iso(p.get("merged_at"))
            if not c or not m:
                continue
            if c < window_start or m < window_start:
                continue

            delta = (m - c).total_seconds()
            per_author.setdefault(author, []).append(delta)

        for author, deltas in per_author.items():
            if not deltas:
                continue
            snapshots.append({
                "metric": "pr_cycle_time_seconds_by_author",
                "target_type": "developer",
                "target_id": author,
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
                "value": mean(deltas),
                "count": len(deltas),
                "computed_at": datetime.now(timezone.utc).isoformat(),
            })

        # ACTIVITY METRIC (experimental): commit_count_per_developer
        commits = read_commits_fallback()

        window_commits = [
            c for c in commits
            if c.get("committed_at") and c["committed_at"] >= window_start
        ]

        commits_by_dev = {}
        for c in window_commits:
            anon_id = (c.get("author") or {}).get("anon_id")
            if not anon_id:
                continue
            commits_by_dev.setdefault(anon_id, []).append(c)

        for anon_id, items in commits_by_dev.items():
            snapshots.append({
                "metric": "commit_count_per_developer",
                "target_type": "developer",
                "target_id": anon_id,
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
                "value": len(items),
                "count": len(items),
                "computed_at": datetime.now(timezone.utc).isoformat(),
            })

        if snapshots:
            with open(METRICS_FALLBACK, "a", encoding="utf-8") as f:
                for s in snapshots:
                    f.write(json.dumps(s, default=str) + "\n")

        self.stdout.write(self.style.SUCCESS(f"Computed {len(snapshots)} snapshot(s) and wrote to {METRICS_FALLBACK}"))