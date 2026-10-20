"""
Demand forecasts and daily sales summaries.

The forecast is a transparent trailing-average estimate, labelled
`moving_average_v1` in the database — it is NOT a trained statistical model.
It gives the dashboard a sensible "expected sales next 7 days" per product,
with a confidence band derived from how much that product's daily sales
actually vary. Swap `generate_demand_forecasts` for a real model later
without touching the callers.
"""
import math
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate

from ..models import DailySalesSummary, DemandForecast, quantize_money
from .analytics import cogs_expression, live_transactions
from .formatting import day_start, local_now, store_tz

MODEL_VERSION = 'moving_average_v1'
WINDOW_DAYS = 28
HORIZON_DAYS = 7
Z_80 = 1.28  # 80% interval


def generate_demand_forecasts(store, window_days=WINDOW_DAYS, horizon_days=HORIZON_DAYS):
    """
    (Re)generate the next-7-days forecast for every active product that sold
    during the trailing window. Completed days only (today is still open).
    Returns the number of forecasts written.
    """
    today = local_now(store).date()
    window_start = day_start(store, today - timedelta(days=window_days))
    window_end = day_start(store, today)
    forecast_date = today + timedelta(days=1)

    rows = (
        live_transactions(store)
        .filter(transaction_date__gte=window_start, transaction_date__lt=window_end, product__isnull=False)
        .annotate(day=TruncDate('transaction_date', tzinfo=store_tz(store)))
        .values('product', 'day')
        .annotate(qty=Sum('quantity_sold'))
    )
    per_product = defaultdict(dict)
    for row in rows:
        per_product[row['product']][row['day']] = row['qty'] or 0

    products = {p.id: p for p in store.products.filter(is_active=True, id__in=list(per_product.keys()))}

    written = 0
    with transaction.atomic():
        for product_id, by_day in per_product.items():
            product = products.get(product_id)
            if product is None:
                continue
            series = [by_day.get(today - timedelta(days=i), 0) for i in range(1, window_days + 1)]
            mean = sum(series) / window_days
            variance = sum((x - mean) ** 2 for x in series) / window_days
            std = math.sqrt(variance)

            predicted = mean * horizon_days
            band = Z_80 * std * math.sqrt(horizon_days)
            active_days = sum(1 for x in series if x > 0)
            confidence = round(min(0.9, 0.4 + 0.5 * active_days / window_days), 2)

            DemandForecast.objects.update_or_create(
                product=product, forecast_date=forecast_date,
                horizon=DemandForecast.Horizon.D7, model_version=MODEL_VERSION,
                defaults={
                    'store': store,
                    'predicted_quantity': round(predicted, 2),
                    'predicted_revenue': quantize_money(Decimal(str(round(predicted, 2))) * product.selling_price),
                    'confidence_lower': round(max(0.0, predicted - band), 2),
                    'confidence_upper': round(predicted + band, 2),
                    'confidence_score': confidence,
                    'model_metadata': {
                        'method': 'trailing mean of daily units',
                        'window_days': window_days,
                        'horizon_days': horizon_days,
                        'days_with_sales': active_days,
                    },
                },
            )
            written += 1

        DemandForecast.objects.filter(
            store=store, model_version=MODEL_VERSION, forecast_date__lt=forecast_date
        ).delete()
    return written


def ensure_forecasts(store):
    """Generate today's forecast set if it doesn't exist yet (cheap no-op otherwise)."""
    forecast_date = local_now(store).date() + timedelta(days=1)
    if not DemandForecast.objects.filter(
        store=store, model_version=MODEL_VERSION, forecast_date=forecast_date
    ).exists():
        generate_demand_forecasts(store)


def upcoming_forecasts(store, limit=5):
    forecast_date = local_now(store).date() + timedelta(days=1)
    return list(
        DemandForecast.objects.filter(
            store=store, model_version=MODEL_VERSION, forecast_date=forecast_date
        ).select_related('product').order_by('-predicted_quantity')[:limit]
    )


@transaction.atomic
def refresh_daily_summaries(store, date_from, date_to):
    """
    Rebuild DailySalesSummary rows (one per product per local day) for
    [date_from, date_to]. Rebuilding the whole range keeps the table correct
    even after sales are voided. Returns the number of rows written.
    """
    start = day_start(store, date_from)
    end = day_start(store, date_to + timedelta(days=1))
    rows = (
        live_transactions(store)
        .filter(transaction_date__gte=start, transaction_date__lt=end, product__isnull=False)
        .annotate(day=TruncDate('transaction_date', tzinfo=store_tz(store)))
        .values('day', 'product', 'product__name')
        .annotate(
            qty=Sum('quantity_sold'), revenue=Sum('total_amount'),
            cogs=Sum(cogs_expression()), n=Count('id'),
        )
    )

    DailySalesSummary.objects.filter(store=store, date__gte=date_from, date__lte=date_to).delete()
    summaries = []
    for row in rows:
        qty = row['qty'] or 0
        revenue = row['revenue'] or Decimal('0.00')
        weekend = row['day'].weekday() >= 5
        summaries.append(DailySalesSummary(
            store=store,
            product_id=row['product'],
            product_name_snapshot=row['product__name'] or '',
            date=row['day'],
            total_quantity_sold=qty,
            total_revenue=revenue,
            total_cost=row['cogs'] or Decimal('0.00'),
            transaction_count=row['n'],
            average_unit_price=quantize_money(revenue / qty) if qty else Decimal('0.00'),
            is_weekend=weekend,
            seasonality_flag=(
                DailySalesSummary.SeasonalityFlag.WEEKEND if weekend
                else DailySalesSummary.SeasonalityFlag.NORMAL
            ),
        ))
    DailySalesSummary.objects.bulk_create(summaries)
    return len(summaries)
