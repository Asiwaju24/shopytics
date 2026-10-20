"""Demand forecasts, daily summaries, the daily job and dashboard additions."""
import datetime
import io
from decimal import Decimal

from django.core.management import call_command
from django.utils import timezone

from core.models import DailySalesSummary, DemandForecast, TransactionLog
from core.services.analytics import build_sales_trend, build_stock_alerts
from core.services.forecasting import generate_demand_forecasts, refresh_daily_summaries
from core.services.formatting import local_now
from .helpers import ShopTestCase


class ForecastTests(ShopTestCase):

    def add_history(self, product, per_day, days=10):
        now = timezone.now()
        for offset in range(1, days + 1):
            TransactionLog.objects.create(
                store=self.store, product=product, quantity_sold=per_day,
                unit_price=product.selling_price, transaction_date=now - datetime.timedelta(days=offset),
            )

    def test_forecast_is_trailing_average_for_seven_days(self):
        self.add_history(self.product_a, per_day=3, days=28)
        written = generate_demand_forecasts(self.store)
        self.assertEqual(written, 1)
        forecast = DemandForecast.objects.get(product=self.product_a)
        self.assertAlmostEqual(forecast.predicted_quantity, 21.0, places=1)    # 3/day x 7
        self.assertEqual(forecast.predicted_revenue, Decimal('2100.00'))      # x price 100
        self.assertEqual(forecast.model_version, 'moving_average_v1')
        self.assertLessEqual(forecast.confidence_lower, forecast.predicted_quantity)
        self.assertGreaterEqual(forecast.confidence_upper, forecast.predicted_quantity)

    def test_regenerating_does_not_duplicate(self):
        self.add_history(self.product_a, per_day=2)
        generate_demand_forecasts(self.store)
        generate_demand_forecasts(self.store)
        self.assertEqual(DemandForecast.objects.filter(product=self.product_a).count(), 1)

    def test_product_without_sales_gets_no_forecast(self):
        self.add_history(self.product_a, per_day=2)
        generate_demand_forecasts(self.store)
        self.assertFalse(DemandForecast.objects.filter(product=self.product_b).exists())

    def test_dashboard_now_lists_forecasts_and_stock_alerts(self):
        self.add_history(self.product_a, per_day=5, days=14)    # 10 in stock / 5 per day = 2 days left
        response = self.api('get', 'dashboard/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['upcoming_forecast']), 1)
        alerts = response.data['stock_alerts']
        self.assertEqual(str(alerts[0]['product_id']), str(self.product_a.id))
        self.assertIn(alerts[0]['tier'], ('critical', 'low'))
        kpi_keys = [k['key'] for k in response.data['kpis']]
        self.assertEqual(kpi_keys, ['today_revenue', 'today_profit', 'today_orders', 'money_at_risk'])

    def test_out_of_stock_product_with_no_history_is_critical(self):
        self.product_b.current_stock = 0
        self.product_b.save()
        alerts = build_stock_alerts(self.store)
        self.assertEqual(alerts[0]['tier'], 'critical')


class SummaryAndJobTests(ShopTestCase):

    def test_daily_summaries_rebuild_and_follow_voids(self):
        sale_id = self.sell([(self.product_a, 2), (self.product_b, 1)]).data['id']
        today = local_now(self.store).date()
        rows = refresh_daily_summaries(self.store, today, today)
        self.assertEqual(rows, 2)
        summary = DailySalesSummary.objects.get(product=self.product_a, date=today)
        self.assertEqual(summary.total_quantity_sold, 2)
        self.assertEqual(summary.total_revenue, Decimal('200.00'))
        self.assertEqual(summary.total_cost, Decimal('100.00'))

        self.api('post', f'sales/{sale_id}/void/')
        refresh_daily_summaries(self.store, today, today)
        self.assertEqual(DailySalesSummary.objects.count(), 0)

    def test_run_daily_jobs_command(self):
        self.sell([(self.product_a, 1)])
        out = io.StringIO()
        call_command('run_daily_jobs', stdout=out)
        self.assertIn('Test Shop', out.getvalue())
        self.assertTrue(DailySalesSummary.objects.filter(store=self.store).exists())

    def test_sales_trend_uses_store_local_day(self):
        self.sell([(self.product_a, 1)])
        series = build_sales_trend(self.store, days=7)
        self.assertEqual(series[-1]['date'], local_now(self.store).date().isoformat())
        self.assertEqual(series[-1]['revenue'], 100.0)

    def test_voided_sale_disappears_from_dashboard_numbers(self):
        sale_id = self.sell([(self.product_a, 3)]).data['id']
        before = self.api('get', 'dashboard/').data['kpis'][0]
        self.assertEqual(Decimal(before['value']), Decimal('300.00'))
        self.api('post', f'sales/{sale_id}/void/')
        after = self.api('get', 'dashboard/').data['kpis'][0]
        self.assertEqual(Decimal(after['value']), Decimal('0.00'))


class PosImportExtraTests(ShopTestCase):

    CSV = (
        "date,product,quantity,unit_price,reference\n"
        "2026-01-05,Product A,2,100,REF-1\n"
        "2026-01-06,Product A,1,100,REF-2\n"
    )

    def upload(self, csv_text, **extra):
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile('sales.csv', csv_text.encode(), content_type='text/csv')
        payload = {'file': file, 'source': 'CSV_IMPORT', 'dry_run': 'false'}
        payload.update(extra)
        return self.client.post(
            '/api/transactions/upload/', payload, format='multipart',
            HTTP_X_STORE_ID=str(self.store.id),
        )

    def test_reimporting_same_file_is_skipped_as_duplicates(self):
        first = self.upload(self.CSV)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['created_transactions'], 2)
        second = self.upload(self.CSV)
        self.assertEqual(second.data['created_transactions'], 0)
        self.assertEqual(second.data['duplicate_rows'], 2)
        self.assertEqual(TransactionLog.objects.count(), 2)

    def test_import_leaves_stock_alone_by_default(self):
        self.upload(self.CSV)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)

    def test_import_can_deduct_stock_through_the_ledger(self):
        response = self.upload(self.CSV, update_stock='true')
        self.assertEqual(response.status_code, 200, response.data)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 7)

    def test_import_refuses_to_oversell_when_updating_stock(self):
        big = "date,product,quantity,unit_price,reference\n2026-01-05,Product A,50,100,BIG-1\n"
        response = self.upload(big, update_stock='true')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TransactionLog.objects.count(), 0)

    def test_xls_is_rejected_with_clear_message(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        file = SimpleUploadedFile('old.xls', b'x', content_type='application/vnd.ms-excel')
        response = self.client.post(
            '/api/transactions/upload/', {'file': file, 'source': 'CSV_IMPORT'}, format='multipart',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, 400)
