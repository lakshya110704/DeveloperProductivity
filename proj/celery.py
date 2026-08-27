import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proj.settings")

app = Celery("proj")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "ingest-github-every-30-min": {
        "task": "core.tasks.run_ingestion",
        "schedule": crontab(minute="*/30"),
    },
    "compute-metrics-hourly": {
        "task": "core.tasks.run_metrics",
        "schedule": crontab(minute=0),
    },
}
