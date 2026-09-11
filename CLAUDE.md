# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DevProd is a backend-first analytics service that turns GitHub activity (PRs, reviews, commits) into developer/repo productivity metrics. Its explicit stance (see README.md): productivity is a set of flow/activity/collaboration signals, not a ranked score — developer identity is SHA-256 hashed (`anon_id_for_email`) before it's ever used as a metric key, and there is no per-developer leaderboard.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# requires a .env with GITHUB_TOKEN, REPOS_TO_POLL, MONGO_URI, REDIS_URL

# Run the dashboard
python manage.py runserver

# Ingestion -> metrics -> reports (the full pipeline, run in order)
python manage.py fetch_github --repos owner/repo1,owner/repo2 --limit 50
python manage.py compute_metrics --days 30
python manage.py generate_report               # all metrics
python manage.py generate_report --metric pr_cycle_time_seconds
python manage.py fetch_linear --limit 250          # requires LINEAR_API_KEY in .env

# Automation (Celery beat runs fetch_github every 30min, compute_metrics hourly)
celery -A proj worker -l info
celery -A proj beat -l info

# Tests — core/tests.py is currently an empty stub; no test suite exists yet
python manage.py test
```

## Product direction

This is becoming a multi-source dev-productivity tool, not a GitHub-only one: GitHub/GitLab cover code-activity signal, Jira/Linear cover task/deadline signal. The intent is a self + manager status view ("on pace" / "lagging"), where "lagging" means *missed deadlines and team completion rate*, not a comparison between people — individual metrics are self-facing, team/project metrics are the manager-facing efficiency signal. Keep that distinction when adding metrics: an individual-scoped metric should read as useful to the person themself, not as a ranking input.

## Architecture

```
GitHub API --> fetch_github.py --> Mongo (pull_requests, raw_events) or JSONL fallback
                                         |
                                         v
                              compute_metrics.py (pandas groupby)
                                         |
                                         v
                         Mongo (metric_snapshots) or JSONL fallback
                                    /          \
                                   v            v
                      views.metrics_api   generate_report.py (matplotlib)
                        (/api/metrics/)        -> data/reports/*.png
                              |
                              v
                      dashboard.html (Chart.js)
```

- **Mongo-first, JSONL-fallback everywhere.** Every write path (`fetch_github.py`, `compute_metrics.py`) tries MongoDB first and transparently degrades to appending local JSONL under `data/` on any connection or write error, so the pipeline never hard-fails and runs with zero infra for local dev/demos. Any change to a write path must preserve both branches — the JSONL reader and the Mongo reader (`views.metrics_api`, `compute_metrics.py`) must stay able to serve whichever store was actually written to.
- **Commit ingestion is JSONL-only.** `build_commit_doc`/`write_commit_fallback` in `fetch_github.py` have no Mongo writer yet, unlike the PR path — this is a known gap, not an oversight, if you're asked to extend it.
- **Django is an orchestration shell, not an ORM.** `core/models.py` is empty. All analytics data lives in Mongo/JSONL; `db.sqlite3` only backs Django's own internals (admin/auth). Don't reach for Django models/migrations to store analytics data — extend the Mongo/JSONL read/write functions in the management commands instead.
- **Metrics are precomputed snapshots, not live aggregations.** `compute_metrics.py` writes `metric_snapshots` (metric, target_type, target_id, window_start/end, value, count) on a schedule; `metrics_api` is a cheap read of that collection/file, never an on-request aggregation.
- **pandas is the aggregation layer for the JSONL fallback path** (`read_pulls_df`, `read_commits_df` in `compute_metrics.py`, using DataFrame `groupby`). The Mongo path still uses a native aggregation pipeline and computes fewer metrics (2 vs. 16) — these two paths have diverged and are not yet at parity; don't assume adding a metric to one adds it to the other.
- **matplotlib (`generate_report.py`) and Chart.js (`dashboard.html`) read the same `metric_snapshots` data for two different consumers** — static PNGs for a scheduled/shareable report vs. a live interactive dashboard. A new metric should generally be visible in both without extra wiring, since both just read snapshot rows.
- **Celery (`proj/celery.py`, `core/tasks.py`) wraps the two management commands as scheduled tasks** (`run_ingestion`, `run_metrics`) against Redis as the broker — it does not contain its own ingestion/metric logic, so changes to `fetch_github`/`compute_metrics` behavior apply automatically to the scheduled runs.
- **`core/connectors/` is the source-agnostic task layer.** `schema.py` defines the canonical task document (provider, task_id, project, title, assignee, status, created_at/due_at/completed_at, url) that every task source normalizes into; `identity.py` holds the shared real-identity + anon_id builder; `base.py` has the Mongo-first/JSONL-fallback `TaskWriter`. `fetch_linear.py` is the first non-GitHub connector, proving the schema generalizes — `compute_metrics.py`'s `read_tasks_df()` reads `data/tasks.jsonl` (or the Mongo `tasks` collection) without caring which connector wrote a given row. A future Jira/Asana connector should normalize into this same shape rather than inventing its own.
- **Deliberate identity split**: PR/commit identity still goes through the original `anon_id_for_email` in `fetch_github.py` (duplicated, not yet pointed at `core/connectors/identity.py` — a known cleanup, not an oversight). Task assignee identity via `core/connectors/identity.py` keeps both the real login/email *and* the anon_id on the same doc, because the self/manager view needs real identity while team-level aggregates should still key off anon_id.
