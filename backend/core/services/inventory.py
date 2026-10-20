"""
Stock ledger. The ONLY place application code should change
`Product.current_stock` — every change is recorded as a `StockMovement`
with before/after levels, so any stock number can be traced and audited.
"""
from django.db import transaction

from ..models import Product, StockMovement
from .errors import BusinessRuleError


def lock_products(store, product_ids, *, active_only=True):
    """
    Load products for update (row-locked on databases that support it) as
    {product_id: Product}. Must be called inside `transaction.atomic()`.
    """
    qs = Product.objects.select_for_update().filter(store=store, id__in=list(product_ids))
    if active_only:
        qs = qs.filter(is_active=True)
    return {p.id: p for p in qs}


def apply_stock_change(product, delta, movement_type, *, user=None, note='',
                       reference_type='', reference_id=''):
    """
    Change `product.current_stock` by `delta` (positive = stock in, negative =
    stock out) and write the ledger row. `product` should be a locked
    instance from `lock_products`. Raises BusinessRuleError rather than ever
    letting stock go negative.
    """
    delta = int(delta)
    before = product.current_stock
    after = before + delta
    if after < 0:
        raise BusinessRuleError(
            f"Not enough stock for {product.name}: {before} in stock, "
            f"but the change needs {abs(delta)}."
        )
    product.current_stock = after
    product.save(update_fields=['current_stock', 'updated_at'])
    return StockMovement.objects.create(
        store_id=product.store_id,
        product=product,
        movement_type=movement_type,
        quantity_change=delta,
        stock_before=before,
        stock_after=after,
        reference_type=reference_type,
        reference_id=str(reference_id) if reference_id else '',
        note=(note or '')[:255],
        created_by=user,
    )


@transaction.atomic
def adjust_stock(product, *, user, mode, quantity, reason, note=''):
    """
    Manual stock correction.
      mode='delta' -> add (positive) / remove (negative) `quantity` units
      mode='set'   -> set stock to exactly `quantity` (a physical count)
    `reason` must be one of StockMovement.Type values usable for manual changes.
    Returns the StockMovement, or None if the count already matched.
    """
    allowed_reasons = (
        StockMovement.Type.ADJUSTMENT,
        StockMovement.Type.DAMAGE,
        StockMovement.Type.EXPIRED,
        StockMovement.Type.COUNT,
    )
    if reason not in allowed_reasons:
        raise BusinessRuleError("Invalid reason for a stock adjustment.")

    locked = Product.objects.select_for_update().get(pk=product.pk)
    quantity = int(quantity)
    if mode == 'set':
        if quantity < 0:
            raise BusinessRuleError("Stock count cannot be negative.")
        delta = quantity - locked.current_stock
    elif mode == 'delta':
        delta = quantity
    else:
        raise BusinessRuleError("mode must be 'delta' or 'set'.")

    if delta == 0:
        return None
    if reason in (StockMovement.Type.DAMAGE, StockMovement.Type.EXPIRED) and delta > 0:
        raise BusinessRuleError("Damaged or expired stock must be a reduction, not an increase.")

    movement = apply_stock_change(
        locked, delta, reason, user=user, note=note,
        reference_type='manual',
    )
    # keep the caller's instance in sync
    product.current_stock = locked.current_stock
    return movement
