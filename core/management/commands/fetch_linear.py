# core/management/commands/fetch_linear.py
"""
Second connector, proving the canonical task schema generalizes beyond
GitHub. Pulls issues from Linear's GraphQL API and normalizes them into
the same task shape fetch_github.py will eventually emit for GitHub
Issues -- compute_metrics.py's deadline/efficiency metrics read that
shape only, never Linear- or GitHub-specific fields.
"""
import requests
from django.core.management.base import BaseCommand
from django.conf import settings

from core.connectors.identity import build_identity
from core.connectors.schema import (
    build_task_doc, STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_DONE, STATUS_CANCELED,
)
from core.connectors.base import TaskWriter

LINEAR_API_URL = "https://api.linear.app/graphql"

# Linear's workflow state "type" values -> canonical status
STATE_TYPE_MAP = {
    "triage": STATUS_OPEN,
    "backlog": STATUS_OPEN,
    "unstarted": STATUS_OPEN,
    "started": STATUS_IN_PROGRESS,
    "completed": STATUS_DONE,
    "canceled": STATUS_CANCELED,
}

ISSUES_QUERY = """
query IssuesPage($first: Int!, $after: String) {
  issues(first: $first, after: $after) {
    nodes {
      id
      title
      url
      createdAt
      dueDate
      completedAt
      state { type }
      assignee { name email displayName }
      team { key }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def normalize_issue(issue: dict) -> dict:
    assignee_raw = issue.get("assignee") or {}
    assignee = build_identity(
        login=assignee_raw.get("name"),
        email=assignee_raw.get("email"),
        display_name=assignee_raw.get("displayName"),
    )
    state_type = (issue.get("state") or {}).get("type")
    status = STATE_TYPE_MAP.get(state_type, STATUS_OPEN)
    team = (issue.get("team") or {}).get("key") or "unknown"

    return build_task_doc(
        provider="linear",
        task_id=issue["id"],
        project=team,
        title=issue.get("title") or "",
        assignee=assignee,
        status=status,
        created_at=issue.get("createdAt"),
        due_at=issue.get("dueDate"),
        completed_at=issue.get("completedAt"),
        url=issue.get("url"),
    )


class Command(BaseCommand):
    help = "Fetch issues from Linear, normalize to the canonical task schema, and upsert (Mongo, falls back to tasks.jsonl)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=250, help="Max issues to fetch")

    def handle(self, *args, **options):
        api_key = getattr(settings, "LINEAR_API_KEY", None)
        if not api_key:
            self.stderr.write("LINEAR_API_KEY not set in settings/.env. Aborting.")
            return

        limit = options.get("limit", 250)
        headers = {"Authorization": api_key, "Content-Type": "application/json"}
        writer = TaskWriter()

        fetched = 0
        cursor = None
        while fetched < limit:
            page_size = min(100, limit - fetched)
            try:
                resp = requests.post(
                    LINEAR_API_URL,
                    json={"query": ISSUES_QUERY, "variables": {"first": page_size, "after": cursor}},
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                self.stderr.write(f"Linear API request failed: {e}")
                break

            if "errors" in payload:
                self.stderr.write(f"Linear API returned errors: {payload['errors']}")
                break

            page = payload["data"]["issues"]
            for issue in page["nodes"]:
                try:
                    doc = normalize_issue(issue)
                    writer.write(doc)
                    fetched += 1
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Skipping issue {issue.get('id')}: {e}"))

            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]

        self.stdout.write(self.style.SUCCESS(f"Fetched and wrote {fetched} Linear task(s)."))
