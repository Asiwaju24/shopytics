from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model

from core.models import Store, Product, Supplier
from core.serializers import ProductSerializer, PurchaseOrderSerializer

User = get_user_model()


class SerializerTenantIsolationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='merchant', password='password123')

        self.store_a = Store.objects.create(name="Store Alpha", owner=self.user)
        self.store_b = Store.objects.create(name="Store Beta", owner=self.user)

        self.supplier_a = Supplier.objects.create(store=self.store_a, name="Supplier A")
        self.supplier_b = Supplier.objects.create(store=self.store_b, name="Supplier B")

        self.product_a = Product.objects.create(
            store=self.store_a,
            name="Product A",
            cost_price=Decimal('10.00'),
            selling_price=Decimal('20.00')
        )

    def test_tenant_fk_boundary_validation_fails_for_cross_store_resource(self):
        """PurchaseOrder in Store A cannot reference Supplier from Store B."""
        data = {
            'supplier': self.supplier_b.id,  # Supplier from Store B
            'items': [
                {
                    'product': self.product_a.id,
                    'quantity_ordered': 5,
                    'unit_cost': '10.00',
                }
            ]
        }
        serializer = PurchaseOrderSerializer(
            data=data,
            context={'store': self.store_a}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('supplier', serializer.errors)

    def test_product_selling_price_below_cost_raises_validation_error(self):
        """Selling price lower than cost price fails validation."""
        data = {
            'sku': 'SKU-001',
            'name': 'Loss Product',
            'cost_price': '50.00',
            'selling_price': '30.00',
        }
        serializer = ProductSerializer(
            data=data,
            context={'store': self.store_a}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('selling_price', serializer.errors)