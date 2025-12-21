# core/views.py
import json
from datetime import datetime, timedelta, timezone
from django.shortcuts import render
from django.http import JsonResponse, HttpResponseServerError
from django.conf import settings
from pathlib import Path
from pymongo import MongoClient

# Helper: get pymongo DB
def get_db():
    uri = getattr(settings, "MONGO_URI", None)
    if not uri:
        raise RuntimeError("MONGO_URI not configured in settings or .env")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    # get_default_database() returns None if the URI doesn't specify a default DB
    db = client.get_default_database()
    if db is None:
        db = client["devprod"]
    return db

def dashboard(request):
    repos_env = getattr(settings, "REPOS_TO_POLL", "")
    repos = [r.strip() for r in repos_env.split(",") if r.strip()]
    context = {"repos": repos}
    return render(request, "dashboard.html", context)

def health(request):
    try:
        db = get_db()
        db.command("ping")
        return JsonResponse({"status": "ok", "mongo": True})
    except Exception as e:
        return HttpResponseServerError(json.dumps({"status": "error", "detail": str(e)}), content_type="application/json")
def metrics_api(request):
    """
    Basic metrics API to return metric_snapshots documents.
    Query params:
      - metric (e.g., pr_cycle_time_seconds)  <-- required
      - target (optional, e.g., repo:owner/repo or developer:anonid)
      - from (ISO date)
      - to (ISO date)
    If Mongo is unavailable, read fallback file at <BASE_DIR>/data/metric_snapshots.jsonl.
    """
    metric = request.GET.get("metric")
    target = request.GET.get("target")
    from_iso = request.GET.get("from")
    to_iso = request.GET.get("to")

    if not metric:
        return JsonResponse({"error": "metric query param required"}, status=400)

    def parse_iso(s):
        if not s:
            return None
        # accept datetime objects through unchanged
        if isinstance(s, datetime):
            dt = s
        else:
            try:
                if isinstance(s, str) and s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
            except Exception:
                try:
                    dt = datetime.fromisoformat(s.split(".")[0])
                except Exception:
                    return None
        # Normalize: return aware UTC datetime
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # Try Mongo first
    try:
        db = get_db()
        coll = db.metric_snapshots
        q = {"metric": metric}
        if target:
            q["target_id"] = target
        if from_iso:
            from_dt = parse_iso(from_iso)
            if from_dt is None:
                return JsonResponse({"error": "invalid from date; use ISO format"}, status=400)
            q["window_start"] = {"$gte": from_dt}
        if to_iso:
            to_dt = parse_iso(to_iso)
            if to_dt is None:
                return JsonResponse({"error": "invalid to date; use ISO format"}, status=400)
            existing = q.get("window_end", {})
            existing["$lte"] = to_dt
            q["window_end"] = existing

        docs = list(coll.find(q).sort([("window_start", 1)]).limit(1000))
        out = []
        for d in docs:
            out.append({
                "metric": d.get("metric"),
                "target_type": d.get("target_type"),
                "target_id": d.get("target_id"),
                "window_start": d.get("window_start").isoformat() if d.get("window_start") else None,
                "window_end": d.get("window_end").isoformat() if d.get("window_end") else None,
                "value": d.get("value"),
                "count": d.get("count"),
            })
        return JsonResponse({"results": out})
    except Exception:
        # Fallback: read JSONL from data/metric_snapshots.jsonl
        try:
            base = getattr(settings, "BASE_DIR", None) or Path(__file__).resolve().parents[2]
            fallback_file = Path(base) / "data" / "metric_snapshots.jsonl"
            results = []
            if fallback_file.exists():
                with open(fallback_file, "r", encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        # basic filtering: metric & optional target & window overlap
                        if obj.get("metric") != metric:
                            continue
                        if target and obj.get("target_id") != target:
                            continue
                        # parse window_start/end from the snapshot (they are ISO strings)
                        ws = parse_iso(obj.get("window_start"))
                        we = parse_iso(obj.get("window_end"))
                        # filter by from_iso/to_iso if provided
                        if from_iso:
                            from_dt = parse_iso(from_iso)
                            if not from_dt:
                                return JsonResponse({"error": "invalid from date; use ISO format"}, status=400)
                            if (we is None) or (we < from_dt):
                                continue
                        if to_iso:
                            to_dt = parse_iso(to_iso)
                            if not to_dt:
                                return JsonResponse({"error": "invalid to date; use ISO format"}, status=400)
                            if (ws is None) or (ws > to_dt):
                                continue
                        # normalize output shape
                        results.append({
                            "metric": obj.get("metric"),
                            "target_type": obj.get("target_type"),
                            "target_id": obj.get("target_id"),
                            "window_start": obj.get("window_start"),
                            "window_end": obj.get("window_end"),
                            "value": obj.get("value"),
                            "count": obj.get("count"),
                        })
            # sort by window_start ISO string (lexicographic works for ISO)
            results = sorted(results, key=lambda r: r.get("window_start") or "")
            return JsonResponse({"results": results})
        except Exception as e2:
            return JsonResponse({"error": "server error", "detail": str(e2)}, status=500)