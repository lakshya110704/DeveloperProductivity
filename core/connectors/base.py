"""
Shared write path for connectors: Mongo-first, JSONL-fallback, same
pattern fetch_github.py already uses for PRs/commits -- pulled out here
so a new connector (fetch_linear.py, and later fetch_jira.py etc.)
gets it for free instead of re-implementing the fallback dance.
"""
import json
from pathlib import Path
from django.conf import settings
from pymongo import MongoClient

BASE_DIR = Path(settings.BASE_DIR)
FALLBACK_DIR = BASE_DIR / "data"
FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
TASKS_FALLBACK = FALLBACK_DIR / "tasks.jsonl"


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


def write_task_fallback(doc: dict):
    with open(TASKS_FALLBACK, "a", encoding="utf-8") as f:
        f.write(json.dumps(doc, default=str) + "\n")


class TaskWriter:
    """
    Tries Mongo `tasks` collection first; degrades to tasks.jsonl on any
    connection or write error, same contract as the rest of the pipeline.
    """

    def __init__(self):
        self.fallback = False
        try:
            db = try_get_db()
            self.tasks_coll = db.tasks
        except Exception:
            self.fallback = True
            self.tasks_coll = None

    def write(self, doc: dict):
        if not self.fallback:
            try:
                self.tasks_coll.update_one(
                    {"provider": doc["provider"], "task_id": doc["task_id"]},
                    {"$set": doc},
                    upsert=True,
                )
                return
            except Exception:
                self.fallback = True
        write_task_fallback(doc)
