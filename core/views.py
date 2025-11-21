# core/views.py
import json
from datetime import datetime, timedelta
from django.shortcuts import render
from django.http import JsonResponse, HttpResponseServerError
from django.conf import settings
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
    metric = request.GET.get("metric")
    target = request.GET.get("target")
    from_iso = request.GET.get("from")
    to_iso = request.GET.get("to")

    if not metric:
        return JsonResponse({"error": "metric query param required"}, status=400)

    def parse_iso(s):
        """Parse ISO datetimes accepting trailing 'Z' (UTC) or +/- offsets."""
        if not s:
            return None
        try:
            # Handle trailing 'Z' (UTC) which fromisoformat doesn't accept
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except Exception:
            return None

    try:
        db = get_db()
        coll = db.metric_snapshots
        q = {"metric": metric}

        if target:
            q["target_id"] = target

        if from_iso:
            from_dt = parse_iso(from_iso)
            if from_dt is None:
                return JsonResponse({"error": "invalid from date; use ISO format (e.g. 2025-11-20T19:30:59Z or 2025-11-20T19:30:59+00:00)"}, status=400)
            q["window_start"] = {"$gte": from_dt}

        if to_iso:
            to_dt = parse_iso(to_iso)
            if to_dt is None:
                return JsonResponse({"error": "invalid to date; use ISO format (e.g. 2025-11-20T19:30:59Z or 2025-11-20T19:30:59+00:00)"}, status=400)
            # ensure we don't overwrite existing dict if both from/to set
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
    except Exception as e:
        return JsonResponse({"error": "server error", "detail": str(e)}, status=500)