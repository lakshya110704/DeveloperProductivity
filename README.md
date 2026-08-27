# 🚀 Developer Productivity & Repo Health Analytics

A backend-first analytics system to track **developer productivity
signals** and **repository health** using GitHub data.

This project focuses on **flow, collaboration, and activity signals**
--- not simplistic or misleading productivity scores.

------------------------------------------------------------------------

## 🎯 Goal

Build an analytics dashboard that helps teams understand:

-   How work flows through the system
-   How developers interact with that flow
-   Where bottlenecks and delays occur
-   How healthy the engineering process is

> ⚠️ This project does **not** aim to rank developers or assign
> productivity scores.

------------------------------------------------------------------------

## 🧠 Philosophy

This system is based on a key principle:

> **Productivity is not a single number --- it is a set of signals.**

We focus on: - ✅ Flow metrics (how work moves) - ✅ Activity signals
(context, not judgment) - ✅ Collaboration patterns - ❌ No rankings -
❌ No "top developer" metrics

------------------------------------------------------------------------

## 🏗️ Architecture

GitHub API → fetch_github.py → JSONL / Mongo → compute_metrics.py
(pandas aggregation) → metric_snapshots → API → Dashboard (Chart.js) /
generate_report.py (matplotlib PNGs)

Ingestion and metric computation can run on demand via `manage.py`, or
on a schedule via Celery beat (`proj/celery.py`, `core/tasks.py`) — see
Automation below.

------------------------------------------------------------------------

## 📊 Current Metrics

`compute_metrics.py` aggregates the JSONL fallback with pandas and
emits 16 metric types per run:

### 🟦 Flow

-   pr_cycle_time_seconds (+ by_author)
-   pr_first_review_seconds
-   pr_avg_review_count
-   pr_approval_rate

### 🟩 Throughput

-   pr_merged_count
-   pr_open_count
-   pr_closed_without_merge_count
-   pr_opened_count_by_author

### 🟪 Size

-   pr_avg_additions
-   pr_avg_deletions
-   pr_avg_changed_files

### 🟨 Activity

-   commit_count_per_developer
-   commit_active_days_per_developer
-   commit_avg_message_length
-   repo_commit_velocity

Run `python manage.py generate_report` to render each series as a
static PNG chart under `data/reports/` (matplotlib), separate from the
live Chart.js dashboard.

------------------------------------------------------------------------

## ⏱️ Automation

Ingestion and metric computation are wired as Celery tasks
(`core/tasks.py`) on a beat schedule (`proj/celery.py`): PRs/commits
are pulled every 30 minutes, metrics recomputed hourly. Requires Redis
(`REDIS_URL` in `.env`) as the broker.

``` bash
celery -A proj worker -l info
celery -A proj beat -l info
```

------------------------------------------------------------------------

## 🛠️ Setup

``` bash
git clone https://github.com/<your-username>/DeveloperProductivty.git
cd DeveloperProductivty
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

------------------------------------------------------------------------

## 🔑 GitHub Token

Create `.env`:

``` env
GITHUB_TOKEN=your_token_here
```

------------------------------------------------------------------------

## 📥 Fetch Data

``` bash
python manage.py fetch_github --repos "owner/repo" --limit 100
```

------------------------------------------------------------------------

## 📈 Compute Metrics

``` bash
python manage.py compute_metrics --days 30
```

------------------------------------------------------------------------

## 📡 API

`/metrics/?metric=pr_cycle_time_seconds`

------------------------------------------------------------------------

## 🔭 Roadmap

-   Developer flow metrics
-   Review metrics
-   Developer dashboards
-   Team insights

------------------------------------------------------------------------

## ⚖️ Ethics

No rankings. No misuse of activity metrics.

------------------------------------------------------------------------

## 📌 Status

Active development
