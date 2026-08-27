"""
Renders metric_snapshots into static PNG trend charts with matplotlib.

Unlike the Chart.js dashboard (client-side, interactive), this produces
static images under data/reports/ suitable for attaching to a summary
email or a PR comment.
"""
import json
from pathlib import Path
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.conf import settings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE_DIR = Path(settings.BASE_DIR)
METRICS_FALLBACK = BASE_DIR / "data" / "metric_snapshots.jsonl"
REPORTS_DIR = BASE_DIR / "data" / "reports"


class Command(BaseCommand):
    help = "Render each metric/target series in metric_snapshots.jsonl to a PNG chart under data/reports/."

    def add_arguments(self, parser):
        parser.add_argument("--metric", help="Only render this metric (default: all)", default=None)

    def handle(self, *args, **options):
        if not METRICS_FALLBACK.exists():
            self.stderr.write(f"No snapshots found at {METRICS_FALLBACK}. Run compute_metrics first.")
            return

        only_metric = options.get("metric")
        series = defaultdict(list)
        with open(METRICS_FALLBACK, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("value") is None:
                    continue
                if only_metric and row.get("metric") != only_metric:
                    continue
                key = (row["metric"], row["target_type"], row["target_id"])
                series[key].append(row)

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        written = 0
        for (metric, target_type, target_id), rows in series.items():
            rows.sort(key=lambda r: r.get("window_end", ""))
            xs = [r["window_end"][:10] for r in rows]
            ys = [r["value"] for r in rows]

            fig, ax = plt.subplots(figsize=(7, 3.2))
            ax.plot(xs, ys, marker="o", linewidth=2)
            ax.set_title(f"{metric} — {target_type}:{target_id[:12]}")
            ax.set_ylabel(metric)
            ax.tick_params(axis="x", rotation=45)
            fig.tight_layout()

            safe_target = "".join(c if c.isalnum() else "_" for c in str(target_id))[:40]
            out_path = REPORTS_DIR / f"{metric}__{target_type}__{safe_target}.png"
            fig.savefig(out_path, dpi=120)
            plt.close(fig)
            written += 1

        self.stdout.write(self.style.SUCCESS(f"Wrote {written} chart(s) to {REPORTS_DIR}"))
