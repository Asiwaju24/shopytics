"""
iPace SME Business Co-Pilot — WhatIf Simulator & Profile Endpoint Tests
===========================================================================
"""
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import User, Store, StoreMembership, Product, TransactionLog


class WhatIfSimulatorTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='simowner', password='password12345')
        self.store = Store.objects.create(owner=self.owner, name='Sim Store', whatsapp_number='+2348000000008')
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        self.product = Product.objects.create(
            store=self.store, sku='S-1', name='Sim Item',
            cost_price=50, selling_price=100, current_stock=1000,
        )
        for day in range(14):
            TransactionLog.objects.create(
                store=self.store, product=self.product,
                quantity_sold=10, unit_price=Decimal('100.00'), total_amount=Decimal('1000.00'),
                transaction_date=timezone.now() - timezone.timedelta(days=day),
            )
        self.client.force_authenticate(user=self.owner)

    def test_projection_reflects_real_product_velocity(self):
        response = self.client.post(
            reverse('simulator-what-if'), {'product_id': str(self.product.id)},
            format='json', HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # ~10 units/day * ₦100 * 7 days ≈ ₦7000, not the old hardcoded ₦10000 baseline.
        self.assertAlmostEqual(float(response.data['projected_revenue']), 7000.0, delta=50.0)

    def test_price_increase_raises_projected_revenue(self):
        baseline = self.client.post(
            reverse('simulator-what-if'), {'product_id': str(self.product.id)},
            format='json', HTTP_X_STORE_ID=str(self.store.id),
        ).data
        higher_price = self.client.post(
            reverse('simulator-what-if'),
            {'product_id': str(self.product.id), 'price_change': '20.00'},
            format='json', HTTP_X_STORE_ID=str(self.store.id),
        ).data
        self.assertGreater(float(higher_price['projected_revenue']), float(baseline['projected_revenue']))

    def test_unknown_product_rejected(self):
        response = self.client.post(
            reverse('simulator-what-if'), {'product_id': '00000000-0000-0000-0000-000000000000'},
            format='json', HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ProfileEndpointTestCase(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # avoid auth-throttle bleed from other test classes
        self.user = User.objects.create_user(
            username='profileuser', password='OldPassword123', email='old@example.com',
        )
        self.client.force_authenticate(user=self.user)

    def test_patch_updates_profile(self):
        response = self.client.patch(reverse('auth-me'), {'full_name': 'New Name'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, 'New Name')

    def test_patch_rejects_taken_username(self):
        User.objects.create_user(username='taken2', password='password12345')
        response = self.client.patch(reverse('auth-me'), {'username': 'taken2'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_password_requires_correct_current_password(self):
        response = self.client.post(reverse('auth-change-password'), {
            'current_password': 'WrongPassword',
            'new_password': 'a-strong-new-password',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_password_succeeds_and_old_password_stops_working(self):
        response = self.client.post(reverse('auth-change-password'), {
            'current_password': 'OldPassword123',
            'new_password': 'a-strong-new-password',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        login = self.client.post(reverse('auth-login'), {
            'username': 'profileuser', 'password': 'a-strong-new-password',
        }, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_change_password_rejects_common_new_password(self):
        response = self.client.post(reverse('auth-change-password'), {
            'current_password': 'OldPassword123',
            'new_password': 'password123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
