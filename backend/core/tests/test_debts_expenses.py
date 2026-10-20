"""Debt repayments, WhatsApp reminders and expenses."""
import datetime
from decimal import Decimal

from core.models import CustomerDebt, DebtPayment, Expense
from core.services.formatting import local_now, whatsapp_link
from .helpers import ShopTestCase


class DebtRepaymentTests(ShopTestCase):

    def setUp(self):
        super().setUp()
        self.debt = CustomerDebt.objects.create(
            store=self.store, customer=self.customer, amount=Decimal('1000.00'),
            due_date=local_now(self.store).date() - datetime.timedelta(days=5),
        )

    def pay(self, amount, **extra):
        return self.api('post', f'debts/{self.debt.id}/record-payment/', {'amount': amount, **extra})

    def test_partial_then_full_payment_settles_debt(self):
        first = self.pay('400.00', method='TRANSFER', note='Part')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(Decimal(first.data['debt']['outstanding_amount']), Decimal('600.00'))
        self.assertFalse(first.data['debt']['is_settled'])

        second = self.pay('600.00')
        self.assertTrue(second.data['debt']['is_settled'])
        self.debt.refresh_from_db()
        self.assertEqual(self.debt.amount_paid, Decimal('1000.00'))
        self.assertIsNotNone(self.debt.settled_at)
        self.assertEqual(DebtPayment.objects.filter(debt=self.debt).count(), 2)

    def test_overpayment_and_settled_debt_rejected(self):
        self.assertEqual(self.pay('1500.00').status_code, 400)
        self.pay('1000.00')
        self.assertEqual(self.pay('1.00').status_code, 400)

    def test_zero_or_negative_payment_rejected(self):
        self.assertEqual(self.pay('0.00').status_code, 400)
        self.assertEqual(self.pay('-5.00').status_code, 400)

    def test_cashier_may_record_payment_but_not_delete_debt(self):
        self.client.force_authenticate(user=self.cashier)
        self.assertEqual(self.pay('100.00').status_code, 201)
        self.assertEqual(self.api('delete', f'debts/{self.debt.id}/').status_code, 403)

    def test_viewer_cannot_record_payment(self):
        self.client.force_authenticate(user=self.viewer)
        self.assertEqual(self.pay('100.00').status_code, 403)

    def test_payment_history_endpoint(self):
        self.pay('100.00')
        self.pay('50.00')
        response = self.api('get', f'debts/{self.debt.id}/payments/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_debt_with_payments_cannot_be_deleted(self):
        self.pay('100.00')
        self.assertEqual(self.api('delete', f'debts/{self.debt.id}/').status_code, 400)

    def test_amount_paid_cannot_be_edited_directly(self):
        self.api('patch', f'debts/{self.debt.id}/', {'amount_paid': '1000.00'})
        self.debt.refresh_from_db()
        self.assertEqual(self.debt.amount_paid, Decimal('0.00'))

    def test_filters(self):
        overdue = self.results(self.api('get', 'debts/', {'overdue': 'true'}))
        self.assertEqual(len(overdue), 1)
        self.pay('1000.00')
        self.assertEqual(len(self.results(self.api('get', 'debts/', {'settled': 'false'}))), 0)
        self.assertEqual(len(self.results(self.api('get', 'debts/', {'settled': 'true'}))), 1)

    def test_reminders_return_whatsapp_links_and_count(self):
        response = self.api('post', 'debts/send-reminders/', {
            'debt_ids': [str(self.debt.id)], 'tone': 'firm',
        })
        self.assertEqual(response.status_code, 202, response.data)
        reminder = response.data['reminders'][0]
        self.assertTrue(reminder['whatsapp_url'].startswith('https://wa.me/2348030000001'))
        self.assertIn('Mrs Ade', reminder['message'])
        self.assertIn('₦1,000', reminder['message'])
        self.debt.refresh_from_db()
        self.assertEqual(self.debt.reminder_count, 1)

    def test_customer_outstanding_debt_is_reported(self):
        self.pay('250.00')
        rows = self.results(self.api('get', 'customers/'))
        self.assertEqual(Decimal(rows[0]['outstanding_debt']), Decimal('750.00'))

    def test_customer_with_unpaid_debt_cannot_be_removed(self):
        self.assertEqual(self.api('delete', f'customers/{self.customer.id}/').status_code, 400)
        self.pay('1000.00')
        self.assertEqual(self.api('delete', f'customers/{self.customer.id}/').status_code, 204)
        self.assertEqual(self.results(self.api('get', 'customers/')), [])


class WhatsAppLinkTests(ShopTestCase):
    def test_local_number_gets_country_code(self):
        self.assertEqual(
            whatsapp_link('0803 000 0001', 'Hi there'), 'https://wa.me/2348030000001?text=Hi%20there'
        )

    def test_international_formats(self):
        self.assertTrue(whatsapp_link('+234 803 000 0001', 'x').startswith('https://wa.me/2348030000001'))
        self.assertTrue(whatsapp_link('2348030000001', 'x').startswith('https://wa.me/2348030000001'))
        self.assertTrue(whatsapp_link('0201234567', 'x', 'GHS').startswith('https://wa.me/233'))

    def test_unusable_number_returns_blank(self):
        self.assertEqual(whatsapp_link('12', 'x'), '')
        self.assertEqual(whatsapp_link('', 'x'), '')


class ExpenseTests(ShopTestCase):

    def create(self, **extra):
        payload = {'category': 'RENT', 'description': 'Shop rent', 'amount': '5000.00'}
        payload.update(extra)
        return self.api('post', 'expenses/', payload)

    def test_create_defaults_date_to_today_and_records_user(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['expense_date'], local_now(self.store).date().isoformat())
        self.assertEqual(response.data['recorded_by_name'], 'owner')

    def test_accountant_and_manager_can_manage_expenses(self):
        for user in (self.accountant, self.manager):
            self.client.force_authenticate(user=user)
            self.assertEqual(self.create().status_code, 201)
            self.assertEqual(self.api('get', 'expenses/').status_code, 200)

    def test_cashier_and_viewer_cannot_see_expenses(self):
        for user in (self.cashier, self.viewer):
            self.client.force_authenticate(user=user)
            self.assertEqual(self.api('get', 'expenses/').status_code, 403)
            self.assertEqual(self.create().status_code, 403)

    def test_amount_must_be_positive(self):
        self.assertEqual(self.create(amount='0.00').status_code, 400)
        self.assertEqual(self.create(amount='-10.00').status_code, 400)

    def test_filter_by_date_and_category(self):
        self.create(expense_date='2026-01-10', category='RENT')
        self.create(expense_date='2026-02-10', category='UTILITIES', description='Power')
        rows = self.results(self.api('get', 'expenses/', {'date_from': '2026-02-01', 'date_to': '2026-02-28'}))
        self.assertEqual([r['description'] for r in rows], ['Power'])
        rows = self.results(self.api('get', 'expenses/', {'category': 'RENT'}))
        self.assertEqual(len(rows), 1)

    def test_bad_date_is_400(self):
        self.assertEqual(self.api('get', 'expenses/', {'date_from': 'yesterday'}).status_code, 400)

    def test_update_and_delete(self):
        expense_id = self.create().data['id']
        updated = self.api('patch', f'expenses/{expense_id}/', {'amount': '7000.00'})
        self.assertEqual(Decimal(updated.data['amount']), Decimal('7000.00'))
        self.assertEqual(self.api('delete', f'expenses/{expense_id}/').status_code, 204)
        self.assertEqual(Expense.objects.count(), 0)
