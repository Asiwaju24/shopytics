"""
iPace SME Business Co-Pilot — Recommendation Generator Tests
================================================================
"""
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import (
    User, Store, StoreMembership, Product, TransactionLog,
    Customer, CustomerDebt, AIRecommendation,
)
from core.services.recommendations import (
    generate_recommendations_for_store, expire_stale_recommendations,
)


class RecommendationGeneratorTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='recowner', password='password12345')
        self.store = Store.objects.create(owner=self.owner, name='Rec Store', whatsapp_number='+2348000000007')
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        self.product = Product.objects.create(
            store=self.store, sku='R-1', name='Critical Item',
            cost_price=50, selling_price=100, current_stock=2, reorder_point=5,
        )
        for day in range(5):
            TransactionLog.objects.create(
                store=self.store, product=self.product,
                quantity_sold=3, unit_price=Decimal('100.00'), total_amount=Decimal('300.00'),
                transaction_date=timezone.now() - timezone.timedelta(days=day),
            )

    def test_generates_stockout_recommendation_for_critical_product(self):
        generate_recommendations_for_store(self.store)
        rec = AIRecommendation.objects.get(
            store=self.store, product=self.product, category=AIRecommendation.Category.STOCKOUT_RISK,
        )
        self.assertEqual(rec.priority, AIRecommendation.Priority.CRITICAL)
        self.assertFalse(rec.is_resolved)
        self.assertIsNotNone(rec.suggested_reorder_qty)

    def test_rerunning_does_not_duplicate(self):
        generate_recommendations_for_store(self.store)
        generate_recommendations_for_store(self.store)
        count = AIRecommendation.objects.filter(
            store=self.store, product=self.product, category=AIRecommendation.Category.STOCKOUT_RISK,
        ).count()
        self.assertEqual(count, 1)

    def test_restocking_expires_the_recommendation(self):
        generate_recommendations_for_store(self.store)
        self.product.current_stock = 500
        self.product.save(update_fields=['current_stock'])

        expire_stale_recommendations(self.store)
        rec = AIRecommendation.objects.get(store=self.store, product=self.product)
        self.assertTrue(rec.is_resolved)

    def test_overdue_debt_generates_reminder(self):
        customer = Customer.objects.create(store=self.store, name='Debtor', phone_number='+2348011112222')
        CustomerDebt.objects.create(
            store=self.store, customer=customer, amount=Decimal('5000.00'),
            due_date=(timezone.now() - timezone.timedelta(days=10)).date(),
        )
        generate_recommendations_for_store(self.store)
        rec = AIRecommendation.objects.get(
            store=self.store, customer=customer, category=AIRecommendation.Category.DEBT_REMINDER,
        )
        self.assertFalse(rec.is_resolved)

    def test_dashboard_priority_actions_reflect_generated_recommendations(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(reverse('dashboard-pulse'), HTTP_X_STORE_ID=str(self.store.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['priority_actions']), 1)
