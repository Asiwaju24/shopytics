"""
iPace SME Business Co-Pilot — Recommendation Generator
=========================================================
Rule-based v1 (same philosophy as analytics.py): deterministic, explainable
triggers off the numbers we already compute, not a trained model. Swap in
real ML later without changing callers — this is the seam.

Idempotent: re-running for a store updates existing *unresolved*
recommendations of the same kind instead of piling up duplicates, so it's
safe to call from both `DashboardView.get()` (keeps the feed fresh on every
load) and a scheduled management command (`generate_recommendations`).
"""

from decimal import Decimal

from django.utils import timezone

from ..models import AIRecommendation, CustomerDebt
from .analytics import store_inventory_snapshot, VELOCITY_WINDOW_DAYS

# Rule-based, not model output — kept modest and constant on purpose so it's
# never confused with a real forecast's confidence score.
RULE_BASED_CONFIDENCE = 0.7

REORDER_BUFFER_DAYS = 14  # suggested_reorder_qty covers this many days of runway
OVERDUE_CRITICAL_DAYS = 30


def _upsert(store, category, priority, title, body, *, product=None, customer=None,
            suggested_reorder_qty=None, estimated_financial_impact=None):
    AIRecommendation.objects.update_or_create(
        store=store, category=category, product=product, customer=customer,
        is_resolved=False,
        defaults={
            'priority': priority,
            'title': title,
            'body': body,
            'suggested_reorder_qty': suggested_reorder_qty,
            'estimated_financial_impact': estimated_financial_impact,
            'confidence_score': RULE_BASED_CONFIDENCE,
        },
    )


def generate_stock_recommendations(store, snapshot=None):
    """One STOCKOUT_RISK/REORDER_SUGGESTION recommendation per at-risk product."""
    snapshot = snapshot if snapshot is not None else store_inventory_snapshot(store)

    for s in snapshot:
        product, tier, velocity, days_left = s['product'], s['tier'], s['velocity'], s['days_remaining']

        if tier not in ('critical', 'low'):
            continue

        target_stock = round(velocity * REORDER_BUFFER_DAYS) if velocity else product.reorder_point
        reorder_qty = max(target_stock - product.current_stock, 0) or None
        at_risk_revenue = (
            Decimal(str(round(velocity, 2))) * product.selling_price * VELOCITY_WINDOW_DAYS
            if velocity else None
        )

        if tier == 'critical':
            days_text = f"~{days_left} day(s)" if days_left is not None else "very soon"
            _upsert(
                store, AIRecommendation.Category.STOCKOUT_RISK, AIRecommendation.Priority.CRITICAL,
                title=f"{product.name} is about to run out",
                body=(
                    f"At the current sales pace, {product.name} will be out of stock in {days_text} "
                    f"({product.current_stock} units left). Reorder now to avoid lost sales."
                ),
                product=product, suggested_reorder_qty=reorder_qty,
                estimated_financial_impact=at_risk_revenue,
            )
        else:  # low
            _upsert(
                store, AIRecommendation.Category.REORDER_SUGGESTION, AIRecommendation.Priority.WARNING,
                title=f"Consider reordering {product.name}",
                body=(
                    f"{product.name} is running lower than usual ({product.current_stock} units left). "
                    f"A reorder soon would keep you ahead of demand."
                ),
                product=product, suggested_reorder_qty=reorder_qty,
                estimated_financial_impact=at_risk_revenue,
            )


def generate_debt_recommendations(store):
    """One DEBT_REMINDER recommendation per customer with overdue debt."""
    debts = CustomerDebt.objects.filter(store=store, is_settled=False).select_related('customer')
    by_customer = {}
    for debt in debts:
        if debt.days_overdue > 0:
            by_customer.setdefault(debt.customer, []).append(debt)

    for customer, customer_debts in by_customer.items():
        total_outstanding = sum((d.outstanding_amount for d in customer_debts), Decimal('0.00'))
        max_days_overdue = max(d.days_overdue for d in customer_debts)
        priority = (
            AIRecommendation.Priority.CRITICAL if max_days_overdue >= OVERDUE_CRITICAL_DAYS
            else AIRecommendation.Priority.WARNING
        )
        _upsert(
            store, AIRecommendation.Category.DEBT_REMINDER, priority,
            title=f"Follow up with {customer.name} on an overdue balance",
            body=(
                f"{customer.name} owes ₦{total_outstanding:,.0f}, overdue by {max_days_overdue} day(s). "
                f"A reminder could help recover this."
            ),
            customer=customer,
            estimated_financial_impact=total_outstanding,
        )


def generate_recommendations_for_store(store):
    """Run every generator for one store. Safe to call repeatedly."""
    snapshot = store_inventory_snapshot(store)
    generate_stock_recommendations(store, snapshot=snapshot)
    generate_debt_recommendations(store)


def expire_stale_recommendations(store):
    """
    Auto-resolve recommendations whose underlying condition no longer holds
    (e.g. the product was restocked, or the debt was paid off) so the feed
    doesn't show stale alerts. Cheap: just re-checks current unresolved rows
    against a fresh snapshot rather than recomputing everything.
    """
    snapshot = {s['product'].id: s['tier'] for s in store_inventory_snapshot(store)}
    now = timezone.now()

    stock_recs = AIRecommendation.objects.filter(
        store=store, is_resolved=False,
        category__in=[AIRecommendation.Category.STOCKOUT_RISK, AIRecommendation.Category.REORDER_SUGGESTION],
        product__isnull=False,
    )
    for rec in stock_recs:
        tier = snapshot.get(rec.product_id)
        if tier not in ('critical', 'low'):
            rec.is_resolved = True
            rec.resolved_at = now
            rec.save(update_fields=['is_resolved', 'resolved_at', 'updated_at'])

    debt_recs = AIRecommendation.objects.filter(
        store=store, is_resolved=False, category=AIRecommendation.Category.DEBT_REMINDER,
        customer__isnull=False,
    )
    for rec in debt_recs:
        still_overdue = CustomerDebt.objects.filter(
            store=store, customer_id=rec.customer_id, is_settled=False
        )
        if not any(d.days_overdue > 0 for d in still_overdue):
            rec.is_resolved = True
            rec.resolved_at = now
            rec.save(update_fields=['is_resolved', 'resolved_at', 'updated_at'])
