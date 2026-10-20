"""
Usage: python manage.py generate_recommendations
Intended to run on a schedule (cron / Celery beat) so the recommendation
feed stays fresh even for stores nobody opens the dashboard for today.
DashboardView also runs this inline per-store on every load, so this
command is a backstop, not the only trigger.
"""
from django.core.management.base import BaseCommand

from core.models import Store
from core.services.recommendations import generate_recommendations_for_store, expire_stale_recommendations


class Command(BaseCommand):
    help = "Generate/refresh rule-based AI recommendations for every active store."

    def handle(self, *args, **options):
        stores = Store.objects.filter(is_active=True)
        count = 0
        for store in stores:
            expire_stale_recommendations(store)
            generate_recommendations_for_store(store)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Refreshed recommendations for {count} store(s)."))
