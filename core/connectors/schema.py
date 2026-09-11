"""
Canonical task schema.

Every connector (fetch_github, fetch_linear, future fetch_jira/fetch_asana)
normalizes into this shape before it's written to Mongo/JSONL. Metrics code
(compute_metrics.py) reads only this shape and never touches a
provider-specific field directly -- that's what lets deadline-adherence
and team-efficiency metrics work the same whether the task came from
GitHub Issues or Linear.

Canonical task document:

{
    "provider": "linear" | "github" | ...,
    "task_id": str,               # provider's native id, unique per provider
    "project": str,                # Linear team key, GitHub repo full_name, etc.
    "title": str,
    "assignee": {                  # see identity.build_identity()
        "login": str | None,
        "email": str | None,
        "display_name": str | None,
        "anon_id": str,
    },
    "status": "open" | "in_progress" | "done" | "canceled",
    "created_at": iso8601 str | None,
    "due_at": iso8601 str | None,       # None if the task has no deadline
    "completed_at": iso8601 str | None, # None if not done
    "url": str | None,
    "last_fetched_at": iso8601 str,
}

STATUS_OPEN / STATUS_IN_PROGRESS / STATUS_DONE / STATUS_CANCELED are the
only values connectors should map their provider's native states into.
"""
from datetime import datetime, timezone

STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_CANCELED = "canceled"

VALID_STATUSES = {STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_DONE, STATUS_CANCELED}

REQUIRED_FIELDS = {
    "provider", "task_id", "project", "title", "assignee",
    "status", "created_at", "due_at", "completed_at", "url", "last_fetched_at",
}


def build_task_doc(
    *,
    provider: str,
    task_id: str,
    project: str,
    title: str,
    assignee: dict,
    status: str,
    created_at=None,
    due_at=None,
    completed_at=None,
    url: str = None,
):
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid task status {status!r}; must be one of {VALID_STATUSES}")

    def _iso(dt):
        if dt is None:
            return None
        if isinstance(dt, str):
            return dt
        return dt.isoformat()

    doc = {
        "provider": provider,
        "task_id": str(task_id),
        "project": project,
        "title": title,
        "assignee": assignee,
        "status": status,
        "created_at": _iso(created_at),
        "due_at": _iso(due_at),
        "completed_at": _iso(completed_at),
        "url": url,
        "last_fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    missing = REQUIRED_FIELDS - doc.keys()
    if missing:
        raise ValueError(f"Task doc missing required fields: {missing}")
    return doc
