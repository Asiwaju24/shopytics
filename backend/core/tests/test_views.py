"""
iPace SME Business Co-Pilot — API View Tests
==============================================
"""
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import (
    User, Store, StoreMembership, Product, Customer, CustomerDebt
)


class StoreAPITestCase(APITestCase):
    """Base class wiring tenant header + fixtures for every test."""

    def setUp(self):
        self.user_a = User.objects.create_user(
            username='owner_a', password='pass1234'
        )
        self.user_b = User.objects.create_user(
            username='owner_b', password='pass1234'
        )

        self.store_a = Store.objects.create(
            owner=self.user_a, name='Store A', whatsapp_number='+2348011111111',
        )
        self.store_b = Store.objects.create(
            owner=self.user_b, name='Store B', whatsapp_number='+2348022222222',
        )

        StoreMembership.objects.create(
            user=self.user_a, store=self.store_a,
            role=StoreMembership.Role.OWNER,
        )
        StoreMembership.objects.create(
            user=self.user_b, store=self.store_b,
            role=StoreMembership.Role.OWNER,
        )

        self.client.force_authenticate(user=self.user_a)

    def api_get(self, url_name, store=None, **kwargs):
        return self.client.get(
            reverse(url_name),
            HTTP_X_STORE_ID=str((store or self.store_a).id),
            **kwargs,
        )

    def api_post(self, url_name, data=None, store=None, **kwargs):
        return self.client.post(
            reverse(url_name),
            data=data or {},
            format='json',
            HTTP_X_STORE_ID=str((store or self.store_a).id),
            **kwargs,
        )


class APIEndpointTestCase(StoreAPITestCase):

    def test_dashboard_endpoint_returns_composite_payload(self):
        """GET /api/dashboard/ returns complete composite payload."""
        response = self.api_get('dashboard-pulse')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in ('store', 'health', 'kpis', 'ticker',
                    'priority_actions', 'upcoming_forecast',
                    'top_movers', 'recent_activity', 'debt_summary'):
            self.assertIn(key, response.data, f"Missing key: {key}")

    def test_products_list_returns_only_current_store_products(self):
        """User A requesting products only receives products belonging to Store A."""
        Product.objects.create(
            store=self.store_a, sku='A-001', name='Store A Product',
            cost_price=100, selling_price=150, current_stock=10,
        )
        Product.objects.create(
            store=self.store_b, sku='B-001', name='Store B Product',
            cost_price=100, selling_price=150, current_stock=10,
        )

        response = self.api_get('product-list')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Pagination-aware extraction
        data = response.data
        results = data['results'] if isinstance(data, dict) else data
        names = [p['name'] for p in results]

        self.assertIn('Store A Product', names)
        self.assertNotIn('Store B Product', names)

    def test_send_debt_reminders_custom_action(self):
        """POST /api/debts/send-reminders/ executes bulk reminder action."""
        customer = Customer.objects.create(
            store=self.store_a, name='Mrs. Kemi', phone_number='+2348033333333',
        )
        debt = CustomerDebt.objects.create(
            store=self.store_a, customer=customer,
            amount=10000, due_date='2025-01-01',
        )

        # NOTE: basename must match urls.py. If registered as 'customerdebt',
        # use 'customerdebt-send-reminders'. If 'debt', use 'debt-send-reminders'.
        url = reverse('debt-send-reminders')
        response = self.api_post(
            'debt-send-reminders',
            data={'debt_ids': [str(debt.id)], 'tone': 'friendly'},
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        debt.refresh_from_db()
        self.assertIsNotNone(debt.last_reminder_sent_at)
        self.assertEqual(debt.reminder_count, 1)