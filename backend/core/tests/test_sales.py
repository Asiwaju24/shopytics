"""Checkout, stock deduction, credit sales and voids."""
from decimal import Decimal

from core.models import CustomerDebt, Sale, StockMovement, TransactionLog
from core.services.analytics import period_totals
from core.services.formatting import today_bounds
from .helpers import ShopTestCase


class CheckoutTests(ShopTestCase):

    def test_checkout_deducts_stock_and_writes_ledger(self):
        response = self.sell([(self.product_a, 3)])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['receipt_number'], 'RCP-000001')
        self.assertEqual(Decimal(response.data['subtotal']), Decimal('300.00'))
        self.assertEqual(Decimal(response.data['tax_amount']), Decimal('30.00'))   # 10% tax
        self.assertEqual(Decimal(response.data['total_amount']), Decimal('330.00'))
        self.assertEqual(Decimal(response.data['amount_paid']), Decimal('330.00'))
        self.assertEqual(len(response.data['lines']), 1)

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 7)
        movement = StockMovement.objects.get(product=self.product_a, movement_type=StockMovement.Type.SALE)
        self.assertEqual(movement.quantity_change, -3)
        self.assertEqual((movement.stock_before, movement.stock_after), (10, 7))

        line = TransactionLog.objects.get(sale__receipt_number='RCP-000001')
        self.assertEqual(line.total_amount, Decimal('300.00'))
        self.assertEqual(line.unit_cost, Decimal('50.00'))

    def test_receipt_numbers_increment(self):
        self.sell([(self.product_a, 1)])
        second = self.sell([(self.product_b, 1)])
        self.assertEqual(second.data['receipt_number'], 'RCP-000002')

    def test_multi_item_cart(self):
        response = self.sell([(self.product_a, 2), (self.product_b, 1)])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['subtotal']), Decimal('400.00'))   # 200 + 200
        self.assertEqual(response.data['item_count'], 3)
        self.product_b.refresh_from_db()
        self.assertEqual(self.product_b.current_stock, 4)

    def test_custom_price_overrides_catalog_price(self):
        response = self.api('post', 'sales/', {
            'items': [{'product': str(self.product_a.id), 'quantity': 2, 'unit_price': '80.00'}],
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['subtotal']), Decimal('160.00'))

    def test_cannot_oversell_and_nothing_changes(self):
        response = self.sell([(self.product_a, 11)])
        self.assertEqual(response.status_code, 400)
        self.assertIn('in stock', response.data['detail'])
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(StockMovement.objects.filter(movement_type=StockMovement.Type.SALE).count(), 0)

    def test_split_lines_of_same_product_are_checked_together(self):
        response = self.sell([(self.product_a, 6), (self.product_a, 6)])
        self.assertEqual(response.status_code, 400)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)

    def test_empty_cart_rejected(self):
        response = self.api('post', 'sales/', {'items': []})
        self.assertEqual(response.status_code, 400)

    def test_product_from_another_store_rejected(self):
        from core.models import Product, Store, StoreMembership, User
        other_owner = User.objects.create_user(username='other', password='Str0ng-pass-123')
        other_store = Store.objects.create(owner=other_owner, name='Other', whatsapp_number='+2348033333333')
        StoreMembership.objects.create(user=other_owner, store=other_store, role=StoreMembership.Role.OWNER)
        foreign = Product.objects.create(
            store=other_store, sku='X-1', name='Foreign', cost_price=1, selling_price=2, current_stock=9,
        )
        response = self.sell([(foreign, 1)])
        self.assertEqual(response.status_code, 400)
        foreign.refresh_from_db()
        self.assertEqual(foreign.current_stock, 9)

    def test_cashier_can_sell(self):
        self.client.force_authenticate(user=self.cashier)
        response = self.sell([(self.product_a, 1)])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['cashier_name'], 'cashier')

    def test_viewer_cannot_sell(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.sell([(self.product_a, 1)])
        self.assertEqual(response.status_code, 403)

    def test_manual_transaction_endpoint_also_deducts_stock(self):
        response = self.api('post', 'transactions/', {
            'product': str(self.product_a.id), 'quantity_sold': 2, 'unit_price': '100.00',
            'transaction_date': '2026-01-01T10:00:00Z',
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 8)

    def test_manual_transaction_cannot_oversell(self):
        response = self.api('post', 'transactions/', {
            'product': str(self.product_a.id), 'quantity_sold': 99, 'unit_price': '100.00',
            'transaction_date': '2026-01-01T10:00:00Z',
        })
        self.assertEqual(response.status_code, 400)


class CreditSaleTests(ShopTestCase):

    def test_credit_sale_creates_debt_and_updates_customer(self):
        response = self.sell(
            [(self.product_a, 2)], customer=str(self.customer.id), payment_method='CREDIT',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['amount_paid']), Decimal('0.00'))
        self.assertEqual(Decimal(response.data['balance_due']), Decimal('220.00'))
        debt = CustomerDebt.objects.get(customer=self.customer)
        self.assertEqual(debt.amount, Decimal('220.00'))
        self.assertEqual(debt.sale.receipt_number, 'RCP-000001')
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.total_orders, 1)
        self.assertEqual(self.customer.total_lifetime_value, Decimal('220.00'))

    def test_part_payment_creates_debt_for_balance(self):
        response = self.sell(
            [(self.product_a, 2)], customer=str(self.customer.id),
            payment_method='CASH', amount_paid='100.00',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(CustomerDebt.objects.get(customer=self.customer).amount, Decimal('120.00'))

    def test_credit_sale_requires_customer(self):
        response = self.sell([(self.product_a, 1)], payment_method='CREDIT')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)

    def test_overpayment_rejected(self):
        response = self.sell([(self.product_a, 1)], amount_paid='9999.00')
        self.assertEqual(response.status_code, 400)


class VoidTests(ShopTestCase):

    def test_void_restocks_and_removes_from_revenue(self):
        sale_id = self.sell([(self.product_a, 4)], customer=str(self.customer.id)).data['id']
        start, end = today_bounds(self.store)
        self.assertEqual(period_totals(self.store, start, end)['revenue'], Decimal('400.00'))

        response = self.api('post', f'sales/{sale_id}/void/', {'reason': 'Wrong item'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'VOIDED')

        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)
        self.assertEqual(period_totals(self.store, start, end)['revenue'], Decimal('0.00'))
        self.assertTrue(StockMovement.objects.filter(movement_type=StockMovement.Type.SALE_VOID).exists())
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.total_orders, 0)
        self.assertEqual(self.customer.total_lifetime_value, Decimal('0.00'))

    def test_cannot_void_twice(self):
        sale_id = self.sell([(self.product_a, 1)]).data['id']
        self.api('post', f'sales/{sale_id}/void/')
        again = self.api('post', f'sales/{sale_id}/void/')
        self.assertEqual(again.status_code, 400)
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.current_stock, 10)   # restocked once, not twice

    def test_void_removes_unpaid_credit_debt(self):
        sale_id = self.sell(
            [(self.product_a, 1)], customer=str(self.customer.id), payment_method='CREDIT'
        ).data['id']
        self.assertEqual(CustomerDebt.objects.count(), 1)
        self.api('post', f'sales/{sale_id}/void/')
        self.assertEqual(CustomerDebt.objects.count(), 0)

    def test_void_blocked_once_customer_has_repaid_part(self):
        sale_id = self.sell(
            [(self.product_a, 1)], customer=str(self.customer.id), payment_method='CREDIT'
        ).data['id']
        debt = CustomerDebt.objects.get()
        self.api('post', f'debts/{debt.id}/record-payment/', {'amount': '10.00'})
        response = self.api('post', f'sales/{sale_id}/void/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.get(id=sale_id).status, Sale.Status.COMPLETED)

    def test_cashier_cannot_void(self):
        sale_id = self.sell([(self.product_a, 1)]).data['id']
        self.client.force_authenticate(user=self.cashier)
        response = self.api('post', f'sales/{sale_id}/void/')
        self.assertEqual(response.status_code, 403)

    def test_sales_list_filters_by_status(self):
        keep = self.sell([(self.product_a, 1)]).data['id']
        gone = self.sell([(self.product_b, 1)]).data['id']
        self.api('post', f'sales/{gone}/void/')
        voided = self.results(self.api('get', 'sales/', {'status': 'VOIDED'}))
        self.assertEqual([s['id'] for s in voided], [gone])
        completed = self.results(self.api('get', 'sales/', {'status': 'COMPLETED'}))
        self.assertEqual([s['id'] for s in completed], [keep])

    def test_sales_cannot_be_deleted_or_edited(self):
        sale_id = self.sell([(self.product_a, 1)]).data['id']
        self.assertEqual(self.api('delete', f'sales/{sale_id}/').status_code, 405)
        self.assertEqual(self.api('patch', f'sales/{sale_id}/', {'notes': 'x'}).status_code, 405)
