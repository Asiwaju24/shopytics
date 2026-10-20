"""Stock ledger, manual adjustments, product lifecycle and the purchase-order flow."""
from decimal import Decimal

from core.models import Product, PurchaseOrder, StockMovement
from .helpers import ShopTestCase


class StockAdjustmentTests(ShopTestCase):

    def test_adjust_by_delta_and_set(self):
        response = self.api('post', f'products/{self.product_a.id}/adjust-stock/', {
            'mode': 'delta', 'quantity': 5, 'reason': 'ADJUSTMENT', 'note': 'Found extra',
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['product']['current_stock'], 15)

        response = self.api('post', f'products/{self.product_a.id}/adjust-stock/', {
            'mode': 'set', 'quantity': 12, 'reason': 'COUNT',
        })
        self.assertEqual(response.data['product']['current_stock'], 12)
        self.assertEqual(response.data['movement']['quantity_change'], -3)

    def test_damaged_stock_reduces_and_cannot_go_negative(self):
        ok = self.api('post', f'products/{self.product_a.id}/adjust-stock/', {
            'mode': 'delta', 'quantity': -4, 'reason': 'DAMAGE',
        })
        self.assertEqual(ok.status_code, 200, ok.data)
        too_much = self.api('post', f'products/{self.product_a.id}/adjust-stock/', {
            'mode': 'delta', 'quantity': -100, 'reason': 'ADJUSTMENT',
        })
        self.assertEqual(too_much.status_code, 400)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 6)

    def test_damage_cannot_increase_stock(self):
        response = self.api('post', f'products/{self.product_a.id}/adjust-stock/', {
            'mode': 'delta', 'quantity': 3, 'reason': 'DAMAGE',
        })
        self.assertEqual(response.status_code, 400)

    def test_cashier_cannot_adjust_stock(self):
        self.client.force_authenticate(user=self.cashier)
        response = self.api('post', f'products/{self.product_a.id}/adjust-stock/', {
            'mode': 'delta', 'quantity': 1, 'reason': 'ADJUSTMENT',
        })
        self.assertEqual(response.status_code, 403)

    def test_movements_endpoint_filters_by_product(self):
        self.api('post', f'products/{self.product_a.id}/adjust-stock/', {'mode': 'delta', 'quantity': 1})
        self.api('post', f'products/{self.product_b.id}/adjust-stock/', {'mode': 'delta', 'quantity': 1})
        rows = self.results(self.api('get', 'stock-movements/', {'product': str(self.product_a.id)}))
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]['product']), str(self.product_a.id))

    def test_movements_bad_product_id_is_400_not_500(self):
        response = self.api('get', 'stock-movements/', {'product': 'not-a-uuid'})
        self.assertEqual(response.status_code, 400)


class ProductLifecycleTests(ShopTestCase):

    def test_creating_product_with_stock_writes_opening_movement(self):
        response = self.api('post', 'products/', {
            'sku': 'N-1', 'name': 'New', 'cost_price': '10.00', 'selling_price': '20.00', 'current_stock': 7,
        })
        self.assertEqual(response.status_code, 201, response.data)
        movement = StockMovement.objects.get(product_id=response.data['id'])
        self.assertEqual(movement.movement_type, StockMovement.Type.OPENING)
        self.assertEqual(movement.stock_after, 7)

    def test_editing_stock_on_product_form_is_recorded_as_count(self):
        response = self.api('patch', f'products/{self.product_a.id}/', {'current_stock': 4})
        self.assertEqual(response.status_code, 200, response.data)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 4)
        movement = StockMovement.objects.get(product=self.product_a)
        self.assertEqual(movement.movement_type, StockMovement.Type.COUNT)
        self.assertEqual(movement.quantity_change, -6)

    def test_editing_price_does_not_touch_stock_or_ledger(self):
        self.api('patch', f'products/{self.product_a.id}/', {'selling_price': '150.00'})
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_delete_hides_product_and_frees_sku(self):
        response = self.api('delete', f'products/{self.product_a.id}/')
        self.assertEqual(response.status_code, 204)
        names = [p['name'] for p in self.results(self.api('get', 'products/'))]
        self.assertNotIn('Product A', names)
        self.assertTrue(Product.objects.filter(id=self.product_a.id, is_active=False).exists())
        again = self.api('post', 'products/', {
            'sku': 'A-1', 'name': 'Product A v2', 'cost_price': '1.00', 'selling_price': '2.00',
        })
        self.assertEqual(again.status_code, 201, again.data)

    def test_deleted_product_cannot_be_sold(self):
        self.api('delete', f'products/{self.product_a.id}/')
        response = self.sell([(self.product_a, 1)])
        self.assertEqual(response.status_code, 400)

    def test_product_supplier_link_endpoint_works(self):
        created = self.api('post', 'product-suppliers/', {
            'product': str(self.product_a.id), 'supplier': str(self.supplier.id),
            'last_cost_price': '45.00', 'minimum_order_qty': 5,
        })
        self.assertEqual(created.status_code, 201, created.data)
        listed = self.results(self.api('get', 'product-suppliers/'))
        self.assertEqual(len(listed), 1)

    def test_supplier_delete_archives_instead_of_crashing(self):
        response = self.api('delete', f'suppliers/{self.supplier.id}/')
        self.assertEqual(response.status_code, 204)
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_active)
        self.assertEqual(self.results(self.api('get', 'suppliers/')), [])


class PurchaseOrderFlowTests(ShopTestCase):

    @staticmethod
    def item_id(po_data, product):
        return next(i['id'] for i in po_data['items'] if str(i['product']) == str(product.id))

    def create_po(self, qty_a=10, qty_b=4):
        response = self.api('post', 'purchase-orders/', {
            'supplier': str(self.supplier.id),
            'whatsapp_message': 'Please supply',
            'items': [
                {'product': str(self.product_a.id), 'quantity_ordered': qty_a, 'unit_cost': '55.00'},
                {'product': str(self.product_b.id), 'quantity_ordered': qty_b, 'unit_cost': '125.00'},
            ],
        })
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_full_lifecycle_adds_stock_only_on_receive(self):
        po = self.create_po()
        self.assertEqual(po['status'], 'DRAFT')
        self.assertEqual(Decimal(po['total_cost']), Decimal('1050.00'))   # 550 + 500
        self.assertTrue(po['whatsapp_url'].startswith('https://wa.me/2348022222222'))

        sent = self.api('post', f"purchase-orders/{po['id']}/send/")
        self.assertEqual(sent.data['status'], 'SENT')
        confirmed = self.api('post', f"purchase-orders/{po['id']}/confirm/", {})
        self.assertEqual(confirmed.data['status'], 'CONFIRMED')
        self.assertIsNotNone(confirmed.data['expected_delivery'])

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)   # nothing received yet

        items = {str(i['product']): i['id'] for i in confirmed.data['items']}
        partial = self.api('post', f"purchase-orders/{po['id']}/receive/", {
            'items': [{'item_id': items[str(self.product_a.id)], 'quantity': 6}],
        })
        self.assertEqual(partial.status_code, 200, partial.data)
        self.assertEqual(partial.data['status'], 'PARTIAL')
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 16)

        final = self.api('post', f"purchase-orders/{po['id']}/receive/", {
            'items': [
                {'item_id': items[str(self.product_a.id)], 'quantity': 4},
                {'item_id': items[str(self.product_b.id)], 'quantity': 4},
            ],
            'update_cost_price': True,
        })
        self.assertEqual(final.data['status'], 'DELIVERED')
        self.assertIsNotNone(final.data['delivered_at'])
        self.product_a.refresh_from_db()
        self.product_b.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 20)
        self.assertEqual(self.product_b.current_stock, 9)
        self.assertEqual(self.product_a.cost_price, Decimal('55.00'))   # updated from the order
        self.assertEqual(
            StockMovement.objects.filter(movement_type=StockMovement.Type.PURCHASE).count(), 3
        )

    def test_cannot_receive_more_than_ordered(self):
        po = self.create_po(qty_a=5)
        response = self.api('post', f"purchase-orders/{po['id']}/receive/", {
            'items': [{'item_id': self.item_id(po, self.product_a), 'quantity': 6}],
        })
        self.assertEqual(response.status_code, 400)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)

    def test_receive_needs_at_least_one_quantity(self):
        po = self.create_po()
        response = self.api('post', f"purchase-orders/{po['id']}/receive/", {
            'items': [{'item_id': self.item_id(po, self.product_a), 'quantity': 0}],
        })
        self.assertEqual(response.status_code, 400)

    def test_status_cannot_be_set_through_patch(self):
        po = self.create_po()
        response = self.api('patch', f"purchase-orders/{po['id']}/", {'status': 'DELIVERED'})
        self.assertEqual(PurchaseOrder.objects.get(id=po['id']).status, PurchaseOrder.Status.DRAFT)
        self.assertIn(response.status_code, (200, 400))

    def test_only_draft_orders_can_be_edited_or_deleted(self):
        po = self.create_po()
        self.api('post', f"purchase-orders/{po['id']}/send/")
        edit = self.api('put', f"purchase-orders/{po['id']}/", {
            'supplier': str(self.supplier.id),
            'items': [{'product': str(self.product_a.id), 'quantity_ordered': 1, 'unit_cost': '1.00'}],
        })
        self.assertEqual(edit.status_code, 400)
        delete = self.api('delete', f"purchase-orders/{po['id']}/")
        self.assertEqual(delete.status_code, 400)

    def test_cancel_and_state_rules(self):
        po = self.create_po()
        self.assertEqual(self.api('post', f"purchase-orders/{po['id']}/cancel/").data['status'], 'CANCELLED')
        again = self.api('post', f"purchase-orders/{po['id']}/send/")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.api('delete', f"purchase-orders/{po['id']}/").status_code, 204)

    def test_cashier_cannot_receive_goods(self):
        po = self.create_po()
        self.client.force_authenticate(user=self.cashier)
        response = self.api('post', f"purchase-orders/{po['id']}/receive/", {
            'items': [{'item_id': self.item_id(po, self.product_a), 'quantity': 1}],
        })
        self.assertEqual(response.status_code, 403)
