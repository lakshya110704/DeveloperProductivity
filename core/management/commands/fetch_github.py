# core/management/commands/fetch_github.py
import os
import json
import hashlib
from datetime import datetime
from django.core.management.base import BaseCommand
from django.conf import settings
from pymongo import MongoClient, errors
from github import Github
from pathlib import Path

# Where to store fallback data if Mongo is unreachable
BASE_DIR = Path(settings.BASE_DIR)
FALLBACK_DIR = BASE_DIR / "data"
FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
PULLS_FALLBACK = FALLBACK_DIR / "pull_requests.jsonl"
RAW_FALLBACK = FALLBACK_DIR / "raw_events.jsonl"

def anon_id_for_email(email, salt="change_this_secret_salt"):
    import hashlib
    h = hashlib.sha256()
    h.update(((email or "").lower()).encode("utf-8"))
    h.update(salt.encode("utf-8"))
    return h.hexdigest()

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

class Command(BaseCommand):
    help = "Fetch PRs from GitHub (poll) and upsert into MongoDB; falls back to local JSONL if Mongo is unavailable."

    def add_arguments(self, parser):
        parser.add_argument("--repos", help="Comma-separated repos to poll (overrides REPOS_TO_POLL env)", default=None)
        parser.add_argument("--limit", help="Max PRs per repo (default 50)", type=int, default=50)

    def handle(self, *args, **options):
        token = getattr(settings, "GITHUB_TOKEN", None)
        if not token:
            self.stderr.write("GITHUB_TOKEN not set in settings/.env. Aborting.")
            return

        repos_env = options.get("repos") or getattr(settings, "REPOS_TO_POLL", "")
        repos = [r.strip() for r in repos_env.split(",") if r.strip()]
        if not repos:
            self.stderr.write("No repositories configured in REPOS_TO_POLL or passed via --repos. Aborting.")
            return

        g = Github(token)
        db = None
        fallback = False
        try:
            db = try_get_db()
            pulls_coll = db.pull_requests
            raw_coll = db.raw_events
            self.stdout.write(self.style.SUCCESS("Connected to MongoDB."))
        except Exception as e:
            fallback = True
            self.stdout.write(self.style.WARNING(f"Could not connect to MongoDB (will fallback to local files): {e}"))

        ops_count = 0
        for full_name in repos:
            try:
                repo = g.get_repo(full_name)
            except Exception as e:
                self.stderr.write(f"Error accessing repo {full_name}: {e}")
                continue

            pulls = repo.get_pulls(state="all", sort="created", direction="desc")
            fetched = 0
            for pr in pulls:
                if fetched >= options.get("limit", 50):
                    break
                fetched += 1
                try:
                    pr_json = pr.raw_data
                except AttributeError:
                    pr_json = {
                        "number": pr.number,
                        "title": pr.title,
                        "user": {"login": getattr(pr.user, "login", None)},
                        "created_at": getattr(pr, "created_at", None).isoformat() if getattr(pr, "created_at", None) else None,
                        "merged_at": getattr(pr, "merged_at", None).isoformat() if getattr(pr, "merged_at", None) else None,
                        "closed_at": getattr(pr, "closed_at", None).isoformat() if getattr(pr, "closed_at", None) else None,
                    }

                author_email = None
                try:
                    if pr.user:
                        author_email = getattr(pr.user, "email", None)
                except Exception:
                    author_email = None

                doc = {
                    "provider": "github",
                    "repo_full_name": full_name,
                    "pr_number": pr.number,
                    "title": pr.title,
                    "author": {
                        "login": getattr(pr.user, "login", None),
                        "email": author_email,
                        "anon_id": anon_id_for_email(author_email),
                    },
                    "created_at": pr.created_at,
                    "merged_at": pr.merged_at,
                    "closed_at": pr.closed_at,
                    "state": "merged" if getattr(pr, "merged", False) else getattr(pr, "state", None),
                    "additions": getattr(pr, "additions", None),
                    "deletions": getattr(pr, "deletions", None),
                    "changed_files": getattr(pr, "changed_files", None),
                    "raw": pr_json,
                    "last_fetched_at": datetime.utcnow(),
                }

                if not fallback:
                    try:
                        pulls_coll.update_one(
                            {"provider": doc["provider"], "repo_full_name": doc["repo_full_name"], "pr_number": doc["pr_number"]},
                            {"$set": doc, "$setOnInsert": {"first_seen": datetime.utcnow()}},
                            upsert=True,
                        )
                        raw_coll.insert_one({"provider": "github", "event_type": "pr_poll", "received_at": datetime.utcnow(), "payload": pr_json})
                        ops_count += 1
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f"Mongo write failed: {e}. Falling back to file writes."))
                        fallback = True

                if fallback:
                    try:
                        with open(PULLS_FALLBACK, "a", encoding="utf-8") as f:
                            f.write(json.dumps({**doc, "last_fetched_at": doc["last_fetched_at"].isoformat()}, default=str) + "\n")
                        with open(RAW_FALLBACK, "a", encoding="utf-8") as rf:
                            rf.write(json.dumps({"provider": "github", "event_type": "pr_poll", "received_at": datetime.utcnow().isoformat(), "payload": pr_json}, default=str) + "\n")
                        ops_count += 1
                    except Exception as e:
                        self.stderr.write(f"Failed to write fallback files: {e}")

            self.stdout.write(self.style.SUCCESS(f"Fetched {fetched} PRs from {full_name}"))

        self.stdout.write(self.style.SUCCESS(f"Done. Processed {ops_count} PR documents."))