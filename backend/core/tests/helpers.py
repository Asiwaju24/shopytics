"""Shared fixtures for the API tests."""
from decimal import Decimal

from rest_framework.test import APITestCase

from core.models import (
    Customer, Product, Store, StoreMembership, Supplier, User,
)

Role = StoreMembership.Role


class ShopTestCase(APITestCase):
    """
    A store with one owner and a ready-made cashier / manager / accountant /
    viewer, two products and one customer. `self.api(...)` sends requests
    with the store header set.
    """
    TAX = Decimal('10.00')

    def setUp(self):
        self.owner = self.make_user('owner', Role.OWNER)
        self.store = Store.objects.create(
            owner=self.owner, name='Test Shop', whatsapp_number='+2348011111111',
            tax_rate_percent=self.TAX,
        )
        StoreMembership.objects.create(user=self.owner, store=self.store, role=Role.OWNER)

        self.manager = self.make_member('manager', Role.MANAGER)
        self.cashier = self.make_member('cashier', Role.CASHIER)
        self.accountant = self.make_member('accountant', Role.ACCOUNTANT)
        self.viewer = self.make_member('viewer', Role.VIEWER)

        self.product_a = Product.objects.create(
            store=self.store, sku='A-1', name='Product A',
            cost_price=Decimal('50.00'), selling_price=Decimal('100.00'),
            current_stock=10, reorder_point=2,
        )
        self.product_b = Product.objects.create(
            store=self.store, sku='B-1', name='Product B',
            cost_price=Decimal('120.00'), selling_price=Decimal('200.00'),
            current_stock=5, reorder_point=2,
        )
        self.customer = Customer.objects.create(
            store=self.store, name='Mrs Ade', phone_number='08030000001',
        )
        self.supplier = Supplier.objects.create(
            store=self.store, name='Sup Ltd', whatsapp_number='+2348022222222',
            phone_number='+2348022222222', lead_time_days=3,
        )
        self.client.force_authenticate(user=self.owner)

    # -- builders -------------------------------------------------------
    def make_user(self, username, role=None):
        return User.objects.create_user(username=username, password='Str0ng-pass-123')

    def make_member(self, username, role):
        user = self.make_user(username)
        StoreMembership.objects.create(user=user, store=self.store, role=role)
        return user

    # -- requests ---------------------------------------------------------
    def api(self, method, path, data=None, store=None, user=None, **extra):
        if user is not None:
            self.client.force_authenticate(user=user)
        client_method = getattr(self.client, method)
        kwargs = {'HTTP_X_STORE_ID': str((store or self.store).id)}
        kwargs.update(extra)
        if method in ('post', 'put', 'patch'):
            return client_method(f'/api/{path}', data=data or {}, format='json', **kwargs)
        return client_method(f'/api/{path}', data or {}, **kwargs)

    def sell(self, items, **payload):
        """POST /api/sales/ with [(product, qty), ...]."""
        body = {'items': [{'product': str(p.id), 'quantity': q} for p, q in items]}
        body.update(payload)
        return self.api('post', 'sales/', body)

    @staticmethod
    def results(response):
        data = response.data
        return data['results'] if isinstance(data, dict) and 'results' in data else data
