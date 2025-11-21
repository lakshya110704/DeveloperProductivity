# core/management/commands/compute_metrics.py
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

def parse_iso(s):
    if not s:
        return None
    if isinstance(s, datetime):
        dt = s
    else:
        # Accept both "2025-11-20T19:30:59Z" and offset forms
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            try:
                dt = datetime.fromisoformat(s.split(".")[0])
            except Exception:
                return None
    # If dt is naive, assume it's UTC and make it aware
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    # Convert aware datetimes to UTC
    return dt.astimezone(timezone.utc)

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

class Command(BaseCommand):
    help = "Compute PR cycle time metric (pr_cycle_time_seconds) for last N days. Writes to Mongo if available, else to fallback JSONL."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Window in days to compute metrics for")

    def handle(self, *args, **options):
        days = options.get("days", 30)
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=days)

        # Try Mongo first
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
                    "cycle_time_seconds": {"$divide":[ {"$subtract": ["$merged_at", "$created_at"]}, 1000 ]}
                }},
                {"$group": {
                    "_id": "$repo_full_name",
                    "avg_cycle_time_sec": {"$avg": "$cycle_time_seconds"},
                    "count": {"$sum": 1}
                }}
            ]

            results = list(pulls_coll.aggregate(pipeline))
            for r in results:
                doc = {
                    "metric": "pr_cycle_time_seconds",
                    "target_type": "repo",
                    "target_id": r["_id"],
                    "window_start": window_start,
                    "window_end": now,
                    "value": r.get("avg_cycle_time_sec"),
                    "count": r.get("count"),
                    "computed_at": datetime.utcnow()
                }
                snapshots_coll.update_one(
                    {"metric": doc["metric"], "target_type": doc["target_type"], "target_id": doc["target_id"], "window_start": doc["window_start"], "window_end": doc["window_end"]},
                    {"$set": doc},
                    upsert=True
                )

            self.stdout.write(self.style.SUCCESS(f"Wrote {len(results)} metric snapshots to Mongo."))
            return

        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not use Mongo: {e}"))
            self.stdout.write(self.style.WARNING("Falling back to JSONL aggregation."))

        # Fallback aggregation
        pulls = read_pull_fallback()
        per_repo = {}

        for p in pulls:
            c = parse_iso(p.get("created_at"))
            m = parse_iso(p.get("merged_at"))
            if not c or not m:
                continue
            if c < window_start or m < window_start:
                continue

            delta = (m - c).total_seconds()
            repo = p.get("repo_full_name", "unknown")
            per_repo.setdefault(repo, []).append(delta)

        snapshots = []
        for repo, deltas in per_repo.items():
            avg = mean(deltas) if deltas else None
            snap = {
                "metric": "pr_cycle_time_seconds",
                "target_type": "repo",
                "target_id": repo,
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
                "value": avg,
                "count": len(deltas),
                "computed_at": datetime.utcnow().isoformat()
            }
            snapshots.append(snap)

        if snapshots:
            with open(METRICS_FALLBACK, "a", encoding="utf-8") as f:
                for s in snapshots:
                    f.write(json.dumps(s, default=str) + "\n")

        self.stdout.write(self.style.SUCCESS(f"Computed {len(snapshots)} snapshot(s) and wrote to {METRICS_FALLBACK}"))