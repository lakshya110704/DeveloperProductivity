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

GitHub API → fetch_github.py → JSONL / Mongo → compute_metrics.py →
metric_snapshots → API → Dashboard

------------------------------------------------------------------------

## 📊 Current Metrics

### 🟦 Flow Metrics

-   pr_cycle_time_seconds
-   pr_first_review_seconds

### 🟩 Throughput

-   pr_merged_count

### 🟨 Activity

-   commit_count_per_developer

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
