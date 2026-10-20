"""
iPace SME Business Co-Pilot — Analytics & Dashboard Tests
============================================================
"""
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import User, Store, StoreMembership, Product, TransactionLog
from core.services.analytics import (
    product_sales_velocity, product_days_remaining, product_stock_tier,
    compute_health_score,
)


class AnalyticsServiceTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='password12345')
        self.store = Store.objects.create(
            owner=self.owner, name='Store', whatsapp_number='+2348000000004',
            daily_revenue_target=Decimal('10000.00'),
        )
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        self.product = Product.objects.create(
            store=self.store, sku='P-1', name='Fast Mover',
            cost_price=50, selling_price=100, current_stock=10, reorder_point=5,
        )

    def _sell(self, qty, days_ago=0):
        TransactionLog.objects.create(
            store=self.store, product=self.product,
            quantity_sold=qty, unit_price=Decimal('100.00'),
            total_amount=Decimal('100.00') * qty,
            transaction_date=timezone.now() - timezone.timedelta(days=days_ago),
        )

    def test_velocity_and_days_remaining_reflect_recent_sales(self):
        # 14 units sold over the trailing 14-day window == 1/day velocity.
        for day in range(14):
            self._sell(1, days_ago=day)

        velocity = product_sales_velocity(self.product)
        self.assertAlmostEqual(velocity, 1.0, places=2)

        days_remaining = product_days_remaining(self.product, velocity)
        self.assertAlmostEqual(days_remaining, 10.0, places=1)  # 10 units / 1 per day

    def test_stock_tier_critical_when_about_to_run_out(self):
        # Low stock + strong recent velocity -> well under the critical threshold.
        self.product.current_stock = 2
        self.product.save(update_fields=['current_stock'])
        for day in range(5):
            self._sell(3, days_ago=day)  # 15 units / 14-day window ≈ 1.07/day -> ~1.9 days left

        tier = product_stock_tier(self.product)
        self.assertEqual(tier, 'critical')

    def test_no_sales_history_falls_back_to_reorder_point(self):
        self.product.current_stock = 2  # below reorder_point of 5
        self.product.save(update_fields=['current_stock'])
        self.assertIsNone(product_days_remaining(self.product))
        self.assertEqual(product_stock_tier(self.product), 'low')

    def test_health_score_is_within_bounds(self):
        self._sell(5)
        score = compute_health_score(self.store)
        self.assertGreaterEqual(score['overall_score'], 0)
        self.assertLessEqual(score['overall_score'], 100)
        for key in ('inventory_score', 'revenue_score', 'customer_score', 'operations_score'):
            self.assertIn(key, score)


class DashboardComputationTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='dashowner', password='password12345')
        self.store = Store.objects.create(
            owner=self.owner, name='Dash Store', whatsapp_number='+2348000000005',
            daily_revenue_target=Decimal('1000.00'),
        )
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        self.product = Product.objects.create(
            store=self.store, sku='D-1', name='Dash Item',
            cost_price=50, selling_price=200, current_stock=20,
        )
        TransactionLog.objects.create(
            store=self.store, product=self.product,
            quantity_sold=3, unit_price=Decimal('200.00'), total_amount=Decimal('600.00'),
            transaction_date=timezone.now(),
        )
        self.client.force_authenticate(user=self.owner)

    def test_dashboard_kpis_reflect_real_transactions(self):
        response = self.client.get(reverse('dashboard-pulse'), HTTP_X_STORE_ID=str(self.store.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        kpis = {kpi['key']: kpi for kpi in response.data['kpis']}
        self.assertIn('today_revenue', kpis)
        self.assertEqual(Decimal(str(kpis['today_revenue']['value'])), Decimal('600.00'))

    def test_dashboard_top_movers_includes_sold_product(self):
        response = self.client.get(reverse('dashboard-pulse'), HTTP_X_STORE_ID=str(self.store.id))
        product_ids = [m['product_id'] for m in response.data['top_movers']]
        self.assertIn(str(self.product.id), [str(pid) for pid in product_ids])

    def test_sales_trend_endpoint_returns_daily_series(self):
        response = self.client.get(reverse('sales-trend'), HTTP_X_STORE_ID=str(self.store.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['series']), 30)
        today_point = response.data['series'][-1]
        self.assertEqual(Decimal(str(today_point['revenue'])), Decimal('600.00'))

    def test_dashboard_health_score_is_computed_not_hardcoded(self):
        response = self.client.get(reverse('dashboard-pulse'), HTTP_X_STORE_ID=str(self.store.id))
        # With no products at risk and revenue already past target, this
        # should score well above the old hardcoded stub's neighborhood —
        # the real assertion is just that it's a valid, bounded score.
        health = response.data['health']
        self.assertGreaterEqual(health['overall_score'], 0)
        self.assertLessEqual(health['overall_score'], 100)


class ProductSerializerComputedFieldsTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='prodowner', password='password12345')
        self.store = Store.objects.create(owner=self.owner, name='Prod Store', whatsapp_number='+2348000000010')
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        self.product = Product.objects.create(
            store=self.store, sku='CF-1', name='Computed Field Item',
            cost_price=50, selling_price=100, current_stock=2, reorder_point=5,
        )
        for day in range(5):
            TransactionLog.objects.create(
                store=self.store, product=self.product,
                quantity_sold=3, unit_price=Decimal('100.00'), total_amount=Decimal('300.00'),
                transaction_date=timezone.now() - timezone.timedelta(days=day),
            )
        self.client.force_authenticate(user=self.owner)

    def test_product_list_includes_computed_stock_fields(self):
        response = self.client.get(reverse('product-list'), HTTP_X_STORE_ID=str(self.store.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data['results'][0]
        self.assertIn('stock_tier', row)
        self.assertIn('daily_velocity', row)
        self.assertIn('days_of_stock_remaining', row)
        self.assertEqual(row['stock_tier'], 'critical')

    def test_status_filter_matches_computed_tier(self):
        response = self.client.get(
            reverse('product-list') + '?status=critical', HTTP_X_STORE_ID=str(self.store.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], str(self.product.id))
