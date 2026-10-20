"""
Checkout & void logic.

`create_sale` is the single code path that turns a cart into: a Sale
(receipt), one TransactionLog per line (so analytics keep working), stock
deductions in the ledger, customer lifetime stats, and — when the customer
doesn't pay in full — a CustomerDebt. It is all-or-nothing: any failure
rolls the whole thing back.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import (
    Customer, CustomerDebt, Sale, Store, StockMovement, TransactionLog, quantize_money,
)
from .errors import BusinessRuleError
from .formatting import local_now
from .inventory import apply_stock_change, lock_products

DEFAULT_CREDIT_DAYS = 14


@transaction.atomic
def create_sale(*, store, user, items, customer=None, payment_method=Sale.PaymentMethod.CASH,
                amount_paid=None, due_date=None, notes=''):
    """
    items: list of {'product_id': UUID, 'quantity': int, 'unit_price': Decimal|None}
    Returns the created Sale.
    """
    if not items:
        raise BusinessRuleError("Add at least one item to the sale.")

    # Lock the store row first: serialises receipt numbering per store.
    store = Store.objects.select_for_update().get(pk=store.pk)
    sold_at = timezone.now()

    products = lock_products(store, {item['product_id'] for item in items})

    # ---- validate lines + stock availability -------------------------
    needed = {}
    prepared = []
    for item in items:
        product = products.get(item['product_id'])
        if product is None:
            raise BusinessRuleError("One of the products was not found, or is no longer active.")
        quantity = int(item['quantity'])
        if quantity < 1:
            raise BusinessRuleError(f"Quantity for {product.name} must be at least 1.")
        unit_price = item.get('unit_price')
        unit_price = quantize_money(unit_price) if unit_price is not None else product.selling_price
        if unit_price < 0:
            raise BusinessRuleError(f"Price for {product.name} cannot be negative.")
        needed[product.id] = needed.get(product.id, 0) + quantity
        prepared.append((product, quantity, unit_price))

    for product_id, qty in needed.items():
        product = products[product_id]
        if qty > product.current_stock:
            raise BusinessRuleError(
                f"Only {product.current_stock} of {product.name} in stock (you tried to sell {qty})."
            )

    # ---- money -------------------------------------------------------
    subtotal = sum((quantize_money(Decimal(q) * price) for _, q, price in prepared), Decimal('0.00'))
    tax_amount = quantize_money(subtotal * store.tax_rate_percent / Decimal('100'))
    total = subtotal + tax_amount

    if amount_paid is None:
        amount_paid = Decimal('0.00') if payment_method == Sale.PaymentMethod.CREDIT else total
    amount_paid = quantize_money(amount_paid)
    if amount_paid < 0:
        raise BusinessRuleError("Amount paid cannot be negative.")
    if amount_paid > total:
        raise BusinessRuleError("Amount paid cannot be more than the sale total.")
    balance = total - amount_paid
    if balance > 0 and customer is None:
        raise BusinessRuleError(
            "Select a customer to record a credit or part-payment sale — "
            "otherwise there is nobody to owe the balance."
        )

    # ---- receipt -----------------------------------------------------
    last_seq = Sale.objects.filter(store=store).aggregate(m=Max('receipt_seq'))['m'] or 0
    seq = last_seq + 1
    sale = Sale.objects.create(
        store=store,
        receipt_seq=seq,
        receipt_number=f"RCP-{seq:06d}",
        customer=customer,
        cashier=user,
        payment_method=payment_method,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total,
        amount_paid=amount_paid,
        notes=notes or '',
        sold_at=sold_at,
    )

    # ---- lines + stock -----------------------------------------------
    for product, quantity, unit_price in prepared:
        line = TransactionLog(
            store=store, product=product, customer=customer, sale=sale,
            quantity_sold=quantity, unit_price=unit_price,
            unit_cost=product.cost_price, currency=store.currency,
            transaction_date=sold_at, source=TransactionLog.Source.MANUAL,
        )
        line.save()
        apply_stock_change(
            product, -quantity, StockMovement.Type.SALE, user=user,
            note=sale.receipt_number, reference_type='sale', reference_id=sale.id,
        )

    # ---- customer stats + debt ---------------------------------------
    if customer is not None:
        locked_customer = Customer.objects.select_for_update().get(pk=customer.pk)
        locked_customer.total_lifetime_value = quantize_money(locked_customer.total_lifetime_value + total)
        locked_customer.total_orders += 1
        locked_customer.last_purchase_at = sold_at
        locked_customer.save(update_fields=[
            'total_lifetime_value', 'total_orders', 'last_purchase_at', 'updated_at',
        ])

    if balance > 0:
        CustomerDebt.objects.create(
            store=store, customer=customer, sale=sale, amount=balance,
            due_date=due_date or (local_now(store).date() + timedelta(days=DEFAULT_CREDIT_DAYS)),
            notes=f"Balance on {sale.receipt_number}",
        )

    return sale


@transaction.atomic
def void_sale(sale, user, reason=''):
    """
    Cancel a completed sale: put the stock back, take the lines out of every
    revenue/profit figure, reverse the customer's lifetime stats, and remove
    the unpaid debt the sale created. Refuses if the customer has already
    made repayments against that debt (the refund would need handling by hand).
    """
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale.status == Sale.Status.VOIDED:
        raise BusinessRuleError("This sale has already been voided.")

    debts = list(sale.debts.all())
    for debt in debts:
        if debt.amount_paid > 0 or debt.payments.exists():
            raise BusinessRuleError(
                "This credit sale already has repayments recorded against it, "
                "so it cannot be voided automatically."
            )

    lines = list(sale.lines.all())
    products = lock_products(
        sale.store, {line.product_id for line in lines if line.product_id}, active_only=False
    )
    for line in lines:
        product = products.get(line.product_id)
        if product is None:
            continue
        apply_stock_change(
            product, line.quantity_sold, StockMovement.Type.SALE_VOID, user=user,
            note=f"Void of {sale.receipt_number}", reference_type='sale', reference_id=sale.id,
        )
    sale.lines.update(is_voided=True)

    for debt in debts:
        debt.delete()

    if sale.customer_id:
        customer = Customer.objects.select_for_update().get(pk=sale.customer_id)
        customer.total_lifetime_value = max(
            Decimal('0.00'), quantize_money(customer.total_lifetime_value - sale.total_amount)
        )
        customer.total_orders = max(0, customer.total_orders - 1)
        customer.save(update_fields=['total_lifetime_value', 'total_orders', 'updated_at'])

    sale.status = Sale.Status.VOIDED
    sale.voided_at = timezone.now()
    sale.voided_by = user
    sale.void_reason = (reason or '')[:255]
    sale.save(update_fields=['status', 'voided_at', 'voided_by', 'void_reason', 'updated_at'])
    return sale
