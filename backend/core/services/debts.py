"""Customer debt repayments and WhatsApp reminder messages."""

from django.db import transaction
from django.utils import timezone

from ..models import CustomerDebt, DebtPayment, quantize_money
from .errors import BusinessRuleError
from .formatting import format_money, whatsapp_link


@transaction.atomic
def record_payment(debt, *, amount, method=DebtPayment.Method.CASH, note='', user=None):
    debt = CustomerDebt.objects.select_for_update().get(pk=debt.pk)
    if debt.is_settled:
        raise BusinessRuleError("This debt is already fully paid.")

    amount = quantize_money(amount)
    if amount <= 0:
        raise BusinessRuleError("Payment amount must be greater than zero.")
    outstanding = debt.outstanding_amount
    if amount > outstanding:
        raise BusinessRuleError(
            f"Payment is more than the outstanding balance of {format_money(outstanding, debt.store.currency)}."
        )

    payment = DebtPayment.objects.create(
        store=debt.store, debt=debt, amount=amount, method=method,
        note=(note or '')[:255], received_by=user,
    )
    debt.amount_paid = quantize_money(debt.amount_paid + amount)
    if debt.amount_paid >= debt.amount:
        debt.is_settled = True
        debt.settled_at = timezone.now()
    debt.save(update_fields=['amount_paid', 'is_settled', 'settled_at', 'updated_at'])
    return payment, debt


_TEMPLATES = {
    'friendly': (
        "Hello {name}, this is a friendly reminder from {store} about your outstanding "
        "balance of {amount} (due {due_date}). Kindly let us know when you can settle it. Thank you!"
    ),
    'firm': (
        "Hello {name}, your balance of {amount} with {store} was due on {due_date} "
        "and is now overdue. Please arrange payment today."
    ),
    'final': (
        "Dear {name}, this is a final notice. Your balance of {amount} with {store}, "
        "due {due_date}, is still unpaid. Please settle it immediately."
    ),
}


def build_reminders(store, debts, tone='friendly', custom_message=''):
    """
    One entry per debt with the message text and a ready-to-open WhatsApp link.
    Nothing is sent automatically — the person opens the link and presses send.
    """
    template = custom_message.strip() if custom_message and custom_message.strip() else _TEMPLATES.get(tone, _TEMPLATES['friendly'])
    reminders = []
    for debt in debts:
        customer = debt.customer
        message = (
            template
            .replace('{name}', customer.name)
            .replace('{amount}', format_money(debt.outstanding_amount, store.currency))
            .replace('{store}', store.name)
            .replace('{due_date}', debt.due_date.strftime('%d %b %Y'))
        )
        reminders.append({
            'debt_id': str(debt.id),
            'customer_name': customer.name,
            'phone_number': customer.phone_number,
            'outstanding_amount': str(debt.outstanding_amount),
            'message': message,
            'whatsapp_url': whatsapp_link(customer.phone_number, message, store.currency),
        })
    return reminders
