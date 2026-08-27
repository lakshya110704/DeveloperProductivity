from celery import shared_task
from django.core.management import call_command


@shared_task
def run_ingestion():
    """Automated ETL step: pull PRs/commits for the configured repos."""
    call_command("fetch_github")


@shared_task
def run_metrics(days: int = 30):
    """Automated ETL step: recompute metric snapshots for the trailing window."""
    call_command("compute_metrics", days=days)
