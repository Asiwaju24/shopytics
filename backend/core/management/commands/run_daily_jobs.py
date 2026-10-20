"""
Usage: python manage.py run_daily_jobs [--summary-days 3]

Run once or twice a day from cron / a scheduler. For every active store it:
  1. rebuilds the DailySalesSummary rows for the last few days,
  2. regenerates the next-7-days demand forecast,
  3. refreshes AI recommendations (and auto-resolves ones that no longer apply).

Example crontab (every day at 02:00):
    0 2 * * *  cd /app/backend && python manage.py run_daily_jobs
"""
from datetime import timedelta

from django.core.management.base import BaseCommand

from core.models import Store
from core.services.forecasting import generate_demand_forecasts, refresh_daily_summaries
from core.services.formatting import local_now
from core.services.recommendations import expire_stale_recommendations, generate_recommendations_for_store


class Command(BaseCommand):
    help = "Refresh daily summaries, demand forecasts and recommendations for every active store."

    def add_arguments(self, parser):
        parser.add_argument('--summary-days', type=int, default=3,
                            help="How many recent days of DailySalesSummary to rebuild (default 3).")

    def handle(self, *args, **options):
        days = max(1, options['summary_days'])
        stores = Store.objects.filter(is_active=True)
        for store in stores:
            today = local_now(store).date()
            summaries = refresh_daily_summaries(store, today - timedelta(days=days - 1), today)
            forecasts = generate_demand_forecasts(store)
            expire_stale_recommendations(store)
            generate_recommendations_for_store(store)
            self.stdout.write(f"{store.name}: {summaries} summary rows, {forecasts} forecasts, recommendations refreshed.")
        self.stdout.write(self.style.SUCCESS(f"Done — {stores.count()} store(s) processed."))
