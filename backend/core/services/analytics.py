"""
iPace SME Business Co-Pilot — Analytics Service
==================================================
Rule-based numbers the dashboard shows: stock velocity, health score, KPIs,
top movers, sales trend and the activity feed.

None of this is machine learning — it's deterministic aggregation over
`TransactionLog`, which keeps it correct and explainable. Notes:

  * "Today" / "yesterday" are the store's own local days (Store.timezone),
    not UTC days, so a Lagos shop's day ends at midnight Lagos time.
  * Voided sales (TransactionLog.is_voided) are excluded from every figure.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from ..models import AIRecommendation, CustomerDebt, Sale, TransactionLog
from .formatting import day_start, format_money, local_now, store_tz, today_bounds

# Thresholds for the inventory "traffic light" tiers.
CRITICAL_DAYS_THRESHOLD = 2.0
LOW_DAYS_THRESHOLD = 5.0
VELOCITY_WINDOW_DAYS = 14

ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def live_transactions(store=None):
    """TransactionLog rows that still count (not voided), optionally for one store."""
    qs = TransactionLog.objects.filter(is_voided=False)
    return qs.filter(store=store) if store is not None else qs


def cogs_expression():
    """SUM-able expression: unit_cost x quantity (a missing unit_cost counts as 0)."""
    unit_cost = Coalesce(
        F('unit_cost'), Value(Decimal('0.00')),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    return ExpressionWrapper(
        unit_cost * F('quantity_sold'),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )


def period_totals(store, start, end):
    """Revenue, cost of goods, gross profit, units and order count for [start, end)."""
    qs = live_transactions(store).filter(transaction_date__gte=start, transaction_date__lt=end)
    agg = qs.aggregate(
        revenue=Sum('total_amount'),
        cogs=Sum(cogs_expression()),
        units=Sum('quantity_sold'),
        sales=Count('sale', distinct=True),
    )
    revenue = agg['revenue'] or ZERO
    cogs = agg['cogs'] or ZERO
    loose_lines = qs.filter(sale__isnull=True).count()
    return {
        'revenue': revenue,
        'cogs': cogs,
        'gross_profit': revenue - cogs,
        'units': agg['units'] or 0,
        'orders': (agg['sales'] or 0) + loose_lines,
    }


# ---------------------------------------------------------------------------
# Stock velocity & tiers
# ---------------------------------------------------------------------------

def bulk_velocity_map(store, window_days=VELOCITY_WINDOW_DAYS):
    """{product_id: average units sold per day} for every product with recent sales — one query."""
    since = timezone.now() - timedelta(days=window_days)
    rows = (
        live_transactions(store)
        .filter(transaction_date__gte=since, product__isnull=False)
        .values('product')
        .annotate(total=Sum('quantity_sold'))
    )
    return {row['product']: (row['total'] or 0) / window_days for row in rows}


def product_sales_velocity(product, window_days=VELOCITY_WINDOW_DAYS):
    """Average units sold per day over the trailing `window_days`."""
    since = timezone.now() - timedelta(days=window_days)
    total = live_transactions().filter(
        product=product, transaction_date__gte=since
    ).aggregate(total=Sum('quantity_sold'))['total'] or 0
    return total / window_days


def product_days_remaining(product, velocity=None):
    """
    Predicted days of stock left at current sales velocity.
    Returns None when there's no recent sales history to extrapolate from —
    callers should treat None as "insufficient data", not "healthy".
    """
    if velocity is None:
        velocity = product_sales_velocity(product)
    if velocity <= 0:
        return None
    return round(product.current_stock / velocity, 1)


def product_stock_tier(product, velocity=None, days_remaining=None):
    """'critical' | 'low' | 'healthy' — drives the traffic-light UI."""
    if days_remaining is None:
        days_remaining = product_days_remaining(product, velocity)
    if days_remaining is None:
        # No sales history to extrapolate from — fall back to the static
        # reorder point the merchant configured on the product itself.
        if product.current_stock <= 0:
            return 'critical'
        return 'low' if product.current_stock <= product.reorder_point else 'healthy'
    if days_remaining < CRITICAL_DAYS_THRESHOLD:
        return 'critical'
    if days_remaining < LOW_DAYS_THRESHOLD:
        return 'low'
    return 'healthy'


def store_inventory_snapshot(store):
    """
    One pass over the store's active products, annotated with
    velocity/days-remaining/tier. Used by the health score, the inventory
    list filter and the alerts so the numbers always agree. Costs two
    queries regardless of how many products the store has.
    """
    products = list(store.products.filter(is_active=True))
    velocities = bulk_velocity_map(store)
    snapshot = []
    for product in products:
        velocity = velocities.get(product.id, 0.0)
        days_remaining = product_days_remaining(product, velocity)
        tier = product_stock_tier(product, velocity, days_remaining)
        snapshot.append({
            'product': product,
            'velocity': velocity,
            'days_remaining': days_remaining,
            'tier': tier,
        })
    return snapshot


def build_stock_alerts(store, limit=6, snapshot=None):
    """Products that are critical or low, most urgent first."""
    snapshot = snapshot if snapshot is not None else store_inventory_snapshot(store)
    at_risk = [s for s in snapshot if s['tier'] in ('critical', 'low')]
    at_risk.sort(key=lambda s: (
        0 if s['tier'] == 'critical' else 1,
        s['days_remaining'] if s['days_remaining'] is not None else 9999,
        s['product'].current_stock,
    ))
    return [
        {
            'product_id': s['product'].id,
            'product_name': s['product'].name,
            'sku': s['product'].sku,
            'current_stock': s['product'].current_stock,
            'reorder_point': s['product'].reorder_point,
            'days_remaining': s['days_remaining'],
            'tier': s['tier'],
        }
        for s in at_risk[:limit]
    ]


def revenue_between(store, start, end):
    return live_transactions(store).filter(
        transaction_date__gte=start, transaction_date__lt=end
    ).aggregate(total=Sum('total_amount'))['total'] or ZERO


# ---------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------

def compute_health_score(store):
    """
    A transparent, weighted blend of four sub-scores (0-100 each):
      - inventory: penalised by the share of products in critical/low tiers
      - revenue: today's revenue vs. the store's daily_revenue_target
      - customer: penalised by the share of outstanding debts that are overdue
      - operations: penalised by unresolved CRITICAL recommendations
    Weights are equal (25% each) — easy to explain and to audit.
    """
    snapshot = store_inventory_snapshot(store)
    total_products = len(snapshot)
    if total_products:
        critical = sum(1 for s in snapshot if s['tier'] == 'critical')
        low = sum(1 for s in snapshot if s['tier'] == 'low')
        inventory_score = round(100 * (total_products - critical - 0.5 * low) / total_products)
    else:
        inventory_score = 100
    inventory_score = max(0, min(100, inventory_score))

    today_start, today_end = today_bounds(store)
    today_revenue = revenue_between(store, today_start, today_end)
    target = store.daily_revenue_target
    if target and target > 0:
        revenue_score = max(0, min(100, round(float(today_revenue / target * 100))))
    else:
        # No target configured — neutral score rather than a misleading 0 or 100.
        revenue_score = 70

    debts = list(CustomerDebt.objects.filter(store=store, is_settled=False))
    overdue_count = sum(1 for d in debts if d.days_overdue > 0)
    total_debts = len(debts)
    customer_score = 100 if total_debts == 0 else max(
        0, round(100 * (total_debts - overdue_count) / total_debts)
    )

    unresolved_critical = AIRecommendation.objects.filter(
        store=store, is_resolved=False, priority=AIRecommendation.Priority.CRITICAL
    ).count()
    operations_score = max(0, 100 - unresolved_critical * 15)

    overall = round(
        0.25 * inventory_score + 0.25 * revenue_score
        + 0.25 * customer_score + 0.25 * operations_score
    )

    if overall >= 80:
        label = 'Strong'
    elif overall >= 60:
        label = 'Stable'
    elif overall >= 40:
        label = 'Needs Attention'
    else:
        label = 'At Risk'

    insights = []
    if inventory_score < 80:
        insights.append(f"{sum(1 for s in snapshot if s['tier'] in ('critical', 'low'))} product(s) are running low on stock.")
    if target and target > 0 and revenue_score >= 100:
        insights.append("Today's revenue has already hit the daily target.")
    if overdue_count:
        insights.append(f"{overdue_count} customer debt(s) are overdue.")
    if not insights:
        insights.append("No urgent issues detected today.")

    return {
        'overall_score': overall,
        'label': label,
        'inventory_score': inventory_score,
        'revenue_score': revenue_score,
        'customer_score': customer_score,
        'operations_score': operations_score,
        'insights': insights,
    }


# ---------------------------------------------------------------------------
# KPI tiles
# ---------------------------------------------------------------------------

def _delta_percent(current, previous):
    if previous:
        return float((current - previous) / previous * 100)
    return 0.0


def _trend(delta):
    return 'up' if delta > 1 else ('down' if delta < -1 else 'flat')


def build_kpis(store):
    """KPI tiles: today's revenue/profit/orders vs yesterday, and stock value at risk."""
    today_start, today_end = today_bounds(store)
    yesterday_start = today_start - timedelta(days=1)
    currency = store.currency

    today = period_totals(store, today_start, today_end)
    yesterday = period_totals(store, yesterday_start, today_start)

    revenue_delta = _delta_percent(today['revenue'], yesterday['revenue'])
    profit_delta = _delta_percent(today['gross_profit'], yesterday['gross_profit'])
    orders_delta = _delta_percent(Decimal(today['orders']), Decimal(yesterday['orders']))

    target = store.daily_revenue_target
    progress = float(today['revenue'] / target * 100) if target else None

    # Trailing 7-day sparkline of daily revenue — one grouped query.
    today_local = local_now(store).date()
    spark_first = today_local - timedelta(days=6)
    by_day = _revenue_by_day(store, day_start(store, spark_first), today_end)
    sparkline = []
    for i in range(7):
        day = spark_first + timedelta(days=i)
        sparkline.append(float(by_day.get(day, {}).get('revenue', 0)))

    snapshot = store_inventory_snapshot(store)
    at_risk = [s for s in snapshot if s['tier'] in ('critical', 'low')]
    money_at_risk = sum(
        (s['product'].selling_price * Decimal(max(s['product'].current_stock, 0)) for s in at_risk),
        ZERO,
    )

    return [
        {
            'key': 'today_revenue',
            'label': "Today's Revenue",
            'value': today['revenue'],
            'display_value': format_money(today['revenue'], currency),
            'delta_percent': round(revenue_delta, 1),
            'trend_direction': _trend(revenue_delta),
            'target': target,
            'progress_percent': round(progress, 1) if progress is not None else None,
            'sparkline': sparkline,
        },
        {
            'key': 'today_profit',
            'label': "Today's Gross Profit",
            'value': today['gross_profit'],
            'display_value': format_money(today['gross_profit'], currency),
            'delta_percent': round(profit_delta, 1),
            'trend_direction': _trend(profit_delta),
            'target': None,
            'progress_percent': None,
            'sparkline': [],
        },
        {
            'key': 'today_orders',
            'label': "Today's Sales",
            'value': Decimal(today['orders']),
            'display_value': str(today['orders']),
            'delta_percent': round(orders_delta, 1),
            'trend_direction': _trend(orders_delta),
            'target': None,
            'progress_percent': None,
            'sparkline': [],
        },
        {
            'key': 'money_at_risk',
            'label': 'Stock Value at Risk',
            'value': money_at_risk,
            'display_value': format_money(money_at_risk, currency),
            'delta_percent': 0.0,
            'trend_direction': 'flat',
            'target': None,
            'progress_percent': None,
            'sparkline': [],
        },
    ]


def build_ticker(store):
    """One entry per critical stock item plus an overdue-debts summary."""
    items = []
    snapshot = store_inventory_snapshot(store)
    for s in snapshot:
        if s['tier'] == 'critical':
            items.append({
                'icon': 'alert-triangle',
                'label': s['product'].name,
                'value': f"{s['days_remaining']} days left" if s['days_remaining'] is not None else 'No sales data',
                'severity': 'critical',
            })
    open_debts = CustomerDebt.objects.filter(store=store, is_settled=False)
    overdue_count = sum(1 for d in open_debts if d.days_overdue > 0)
    if overdue_count:
        items.append({
            'icon': 'clock',
            'label': 'Overdue customer debts',
            'value': str(overdue_count),
            'severity': 'warning',
        })
    return items


# ---------------------------------------------------------------------------
# Top movers, trend, activity
# ---------------------------------------------------------------------------

def build_top_movers(store, window_days=7, limit=5):
    """Top products by revenue in the trailing window, with delta vs the prior window."""
    now = timezone.now()
    window_start = now - timedelta(days=window_days)
    prior_start = window_start - timedelta(days=window_days)

    current = (
        live_transactions(store).filter(transaction_date__gte=window_start)
        .values('product', 'product__name')
        .annotate(units_sold=Sum('quantity_sold'), revenue=Sum('total_amount'))
        .order_by('-revenue')[:limit]
    )

    movers = []
    for row in current:
        if row['product'] is None:
            continue
        prior_revenue = live_transactions(store).filter(
            product_id=row['product'],
            transaction_date__gte=prior_start, transaction_date__lt=window_start,
        ).aggregate(total=Sum('total_amount'))['total'] or ZERO

        revenue = row['revenue'] or ZERO
        delta = _delta_percent(revenue, prior_revenue)
        movers.append({
            'rank': len(movers) + 1,
            'product_id': row['product'],
            'product_name': row['product__name'],
            'units_sold': row['units_sold'] or 0,
            'revenue': revenue,
            'delta_percent': round(delta, 1),
            'trend_direction': _trend(delta),
        })
    return movers


def _revenue_by_day(store, start, end):
    """{local_date: {'revenue': Decimal, 'count': int}} for [start, end), one query."""
    rows = (
        live_transactions(store)
        .filter(transaction_date__gte=start, transaction_date__lt=end)
        .annotate(day=TruncDate('transaction_date', tzinfo=store_tz(store)))
        .values('day')
        .annotate(revenue=Sum('total_amount'), count=Count('id'))
    )
    return {row['day']: {'revenue': row['revenue'] or ZERO, 'count': row['count']} for row in rows}


def build_sales_trend(store, days=30):
    """
    Daily revenue series for the trailing `days` (in the store's local days),
    each day flagged `is_event` when it deviates >35% from the trailing
    7-day average — a simple, honest rule rather than a statistical model.
    """
    today = local_now(store).date()
    first_day = today - timedelta(days=days - 1)
    by_day = _revenue_by_day(store, day_start(store, first_day), day_start(store, today + timedelta(days=1)))

    daily = []
    for i in range(days):
        day = first_day + timedelta(days=i)
        row = by_day.get(day, {})
        daily.append({
            'date': day.isoformat(),
            'revenue': float(row.get('revenue', 0)),
            'transaction_count': row.get('count', 0),
        })

    for idx, point in enumerate(daily):
        window = [d['revenue'] for d in daily[max(0, idx - 7):idx]]
        rolling_avg = sum(window) / len(window) if window else None
        if rolling_avg and rolling_avg > 0:
            deviation = (point['revenue'] - rolling_avg) / rolling_avg
            point['is_event'] = abs(deviation) > 0.35
            point['deviation_percent'] = round(deviation * 100, 1)
        else:
            point['is_event'] = False
            point['deviation_percent'] = 0.0

    return daily


def build_recent_activity(store, limit=10):
    """Unified feed: recent receipts, loose sale lines, and recent recommendations."""
    activity = []
    currency = store.currency

    recent_sales = (
        Sale.objects.filter(store=store, status=Sale.Status.COMPLETED)
        .annotate(item_count=Count('lines'))
        .order_by('-sold_at')[:limit]
    )
    for sale in recent_sales:
        activity.append({
            'kind': 'sale',
            'title': f"Sale {sale.receipt_number}",
            'subtitle': f"{sale.item_count} item(s) — {format_money(sale.total_amount, currency)}",
            'icon': 'shopping-cart',
            'severity': 'success',
            'timestamp': sale.sold_at,
        })

    loose_lines = (
        live_transactions(store).filter(sale__isnull=True)
        .select_related('product').order_by('-transaction_date')[:limit]
    )
    for txn in loose_lines:
        activity.append({
            'kind': 'sale',
            'title': txn.product.name if txn.product else 'Sale',
            'subtitle': f"{txn.quantity_sold} units — {format_money(txn.total_amount, currency)}",
            'icon': 'shopping-cart',
            'severity': 'success',
            'timestamp': txn.transaction_date,
        })

    recent_recs = AIRecommendation.objects.filter(store=store).order_by('-created_at')[:limit]
    for rec in recent_recs:
        severity = {
            AIRecommendation.Priority.CRITICAL: 'critical',
            AIRecommendation.Priority.WARNING: 'warning',
            AIRecommendation.Priority.INFO: 'info',
        }.get(rec.priority, 'info')
        activity.append({
            'kind': 'ai_alert',
            'title': rec.title,
            'subtitle': rec.body[:120],
            'icon': 'sparkles',
            'severity': severity,
            'timestamp': rec.created_at,
        })

    activity.sort(key=lambda a: a['timestamp'], reverse=True)
    return activity[:limit]
