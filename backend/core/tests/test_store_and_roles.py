"""
iPace SME Business Co-Pilot — Store Onboarding & Role Permission Tests
=========================================================================
"""
from decimal import Decimal
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import User, Store, StoreMembership, Product


class StoreOnboardingTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='founder', password='password12345')
        self.client.force_authenticate(user=self.user)

    def test_create_store_makes_creator_the_owner(self):
        response = self.client.post(reverse('store-list'), {
            'name': 'My New Shop',
            'whatsapp_number': '+2348012345678',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        store = Store.objects.get(id=response.data['id'])
        self.assertEqual(store.owner, self.user)
        membership = StoreMembership.objects.get(store=store, user=self.user)
        self.assertEqual(membership.role, StoreMembership.Role.OWNER)

    def test_list_only_returns_stores_user_belongs_to(self):
        my_store = Store.objects.create(owner=self.user, name='Mine', whatsapp_number='+2348000000001')
        StoreMembership.objects.create(user=self.user, store=my_store, role=StoreMembership.Role.OWNER)

        other_owner = User.objects.create_user(username='other', password='password12345')
        other_store = Store.objects.create(owner=other_owner, name='Not Mine', whatsapp_number='+2348000000002')
        StoreMembership.objects.create(user=other_owner, store=other_store, role=StoreMembership.Role.OWNER)

        response = self.client.get(reverse('store-list'))
        names = [s['name'] for s in response.data['results']] if isinstance(response.data, dict) else [s['name'] for s in response.data]
        self.assertIn('Mine', names)
        self.assertNotIn('Not Mine', names)

    def test_non_owner_cannot_update_store(self):
        store = Store.objects.create(owner=self.user, name='Mine', whatsapp_number='+2348000000001')
        StoreMembership.objects.create(user=self.user, store=store, role=StoreMembership.Role.OWNER)

        staff = User.objects.create_user(username='staff', password='password12345')
        StoreMembership.objects.create(user=staff, store=store, role=StoreMembership.Role.MANAGER)
        self.client.force_authenticate(user=staff)

        response = self.client.patch(
            reverse('store-detail', args=[store.id]), {'name': 'Renamed'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class RoleBasedPermissionTestCase(APITestCase):
    """A VIEWER can read tenant-scoped resources but not write to them."""

    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='password12345')
        self.viewer = User.objects.create_user(username='viewer', password='password12345')
        self.store = Store.objects.create(owner=self.owner, name='Store', whatsapp_number='+2348000000003')
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        StoreMembership.objects.create(user=self.viewer, store=self.store, role=StoreMembership.Role.VIEWER)

    def test_viewer_can_list_products(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get(
            reverse('product-list'), HTTP_X_STORE_ID=str(self.store.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_viewer_cannot_create_product(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            reverse('product-list'),
            {'sku': 'X-1', 'name': 'New Item', 'selling_price': 100, 'cost_price': 50},
            format='json',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_create_product(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            reverse('product-list'),
            {'sku': 'X-2', 'name': 'New Item', 'selling_price': 100, 'cost_price': 50},
            format='json',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cashier_can_create_transaction_but_not_delete_product(self):
        cashier = User.objects.create_user(username='cashier', password='password12345')
        StoreMembership.objects.create(user=cashier, store=self.store, role=StoreMembership.Role.CASHIER)
        product = Product.objects.create(
            store=self.store, sku='C-1', name='Cashier Item',
            cost_price=50, selling_price=100, current_stock=10,
        )

        self.client.force_authenticate(user=cashier)

        txn_response = self.client.post(
            reverse('transaction-list'),
            {
                'product': str(product.id), 'quantity_sold': 2, 'unit_price': '100.00',
                'transaction_date': '2026-01-01T10:00:00Z',
            },
            format='json',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(txn_response.status_code, status.HTTP_201_CREATED)

        delete_response = self.client.delete(
            reverse('product-detail', args=[product.id]), HTTP_X_STORE_ID=str(self.store.id)
        )
        self.assertEqual(delete_response.status_code, status.HTTP_403_FORBIDDEN)


class DispatchPurchaseOrderTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='powner', password='password12345')
        self.store = Store.objects.create(owner=self.owner, name='PO Store', whatsapp_number='+2348000000009')
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        from core.models import Supplier
        self.supplier = Supplier.objects.create(
            store=self.store, name='Test Supplier', phone_number='+2348022220000',
        )
        self.product = Product.objects.create(
            store=self.store, sku='PO-1', name='PO Item',
            cost_price=500, selling_price=800, current_stock=10,
        )
        self.client.force_authenticate(user=self.owner)

    def test_dispatch_creates_po_with_correct_items(self):
        response = self.client.post(
            reverse('purchase-order-dispatch'),
            {
                'supplier_id': str(self.supplier.id),
                'items': [{'product_id': str(self.product.id), 'quantity': 5}],
            },
            format='json',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(len(response.data['items']), 1)
        self.assertEqual(response.data['items'][0]['quantity_ordered'], 5)
        self.assertEqual(Decimal(response.data['total_cost']), Decimal('2500.00'))
        self.assertIn('Test Supplier', response.data['whatsapp_message'])
