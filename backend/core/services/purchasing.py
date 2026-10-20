"""
Purchase-order lifecycle: DRAFT -> SENT -> CONFIRMED -> PARTIAL/DELIVERED
(or CANCELLED). Receiving goods is what actually increases stock.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import ProductSupplier, PurchaseOrder, StockMovement
from .errors import BusinessRuleError
from .formatting import local_now
from .inventory import apply_stock_change, lock_products

PO = PurchaseOrder.Status


def _lock_po(po):
    return PurchaseOrder.objects.select_for_update().get(pk=po.pk)


def _require(po, allowed, verb):
    if po.status not in allowed:
        raise BusinessRuleError(
            f"A purchase order that is {po.get_status_display().lower()} cannot be {verb}."
        )


@transaction.atomic
def mark_sent(po):
    po = _lock_po(po)
    _require(po, (PO.DRAFT,), 'marked as sent')
    po.status = PO.SENT
    po.sent_at = timezone.now()
    po.save(update_fields=['status', 'sent_at', 'updated_at'])
    return po


@transaction.atomic
def confirm(po, expected_delivery=None):
    po = _lock_po(po)
    _require(po, (PO.SENT,), 'confirmed')
    if expected_delivery is None:
        expected_delivery = local_now(po.store).date() + timedelta(days=po.supplier.lead_time_days)
    po.status = PO.CONFIRMED
    po.confirmed_at = timezone.now()
    po.expected_delivery = expected_delivery
    po.save(update_fields=['status', 'confirmed_at', 'expected_delivery', 'updated_at'])
    return po


@transaction.atomic
def cancel(po):
    po = _lock_po(po)
    _require(po, (PO.DRAFT, PO.SENT, PO.CONFIRMED), 'cancelled')
    po.status = PO.CANCELLED
    po.save(update_fields=['status', 'updated_at'])
    return po


@transaction.atomic
def receive(po, user, received, update_cost_price=False):
    """
    received: list of {'item_id': UUID, 'quantity': int} — units received NOW.
    Adds the units to stock (ledger entry per item), records the supplier's
    latest cost, and moves the order to PARTIAL or DELIVERED.
    """
    po = _lock_po(po)
    _require(po, (PO.DRAFT, PO.SENT, PO.CONFIRMED, PO.PARTIAL), 'received')

    items = {item.id: item for item in po.items.all()}
    products = lock_products(po.store, {i.product_id for i in items.values()}, active_only=False)

    any_received = False
    for entry in received:
        qty = int(entry['quantity'])
        if qty <= 0:
            continue
        item = items.get(entry['item_id'])
        if item is None:
            raise BusinessRuleError("One of the received items is not part of this order.")
        remaining = item.quantity_ordered - item.quantity_received
        if qty > remaining:
            raise BusinessRuleError(
                f"Cannot receive {qty} of {products[item.product_id].name}: "
                f"only {remaining} still outstanding on this order."
            )
        product = products[item.product_id]
        item.quantity_received += qty
        item.save(update_fields=['quantity_received', 'updated_at'])
        apply_stock_change(
            product, qty, StockMovement.Type.PURCHASE, user=user,
            note=f"Received from {po.supplier.name}",
            reference_type='purchase_order', reference_id=po.id,
        )
        if update_cost_price:
            product.cost_price = item.unit_cost
            product.save(update_fields=['cost_price', 'updated_at'])
        ProductSupplier.objects.filter(
            product_id=item.product_id, supplier_id=po.supplier_id
        ).update(last_cost_price=item.unit_cost)
        any_received = True

    if not any_received:
        raise BusinessRuleError("Enter a received quantity for at least one item.")

    fully_received = all(i.quantity_received >= i.quantity_ordered for i in items.values())
    if fully_received:
        po.status = PO.DELIVERED
        po.delivered_at = timezone.now()
    else:
        po.status = PO.PARTIAL
    po.save(update_fields=['status', 'delivered_at', 'updated_at'])
    return po
