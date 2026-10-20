"""
iPace SME Business Co-Pilot — Analytics & Composite Views
==========================================================
"""

from decimal import Decimal
from django.utils import timezone
from rest_framework import permissions, status, views
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from .base import IsStoreMember
from ..models import CustomerDebt, AIRecommendation
from ..services.analytics import (
    compute_health_score, build_kpis, build_ticker,
    build_top_movers, build_recent_activity, build_sales_trend,
    build_stock_alerts, product_sales_velocity, VELOCITY_WINDOW_DAYS,
)
from ..services.forecasting import ensure_forecasts, upcoming_forecasts
from ..services.recommendations import generate_recommendations_for_store, expire_stale_recommendations
from ..serializers import (
    DashboardSerializer, WhatIfInputSerializer, WhatIfOutputSerializer, SalesTrendResponseSerializer
)


class DashboardView(views.APIView):
    """
    GET /api/dashboard/
    Serves composite dashboard payload.
    """
    permission_classes = [permissions.IsAuthenticated, IsStoreMember]

    @extend_schema(responses=DashboardSerializer)
    def get(self, request):
        store = getattr(request, 'store')

        # Keep the recommendation feed fresh on every load — cheap (same
        # snapshot the health score already computes) and idempotent.
        expire_stale_recommendations(store)
        generate_recommendations_for_store(store)
        ensure_forecasts(store)

        debts = CustomerDebt.objects.filter(store=store, is_settled=False)
        debt_summary = {
            'total_outstanding': sum(d.outstanding_amount for d in debts) or Decimal('0.00'),
            'debtor_count': debts.values('customer').distinct().count(),
            'overdue_count': sum(1 for d in debts if d.days_overdue > 0),
            'oldest_overdue_days': max((d.days_overdue for d in debts), default=0),
            'top_debts': debts.order_by('-amount')[:5]
        }

        recs = AIRecommendation.objects.filter(store=store, is_resolved=False)
        insight_banner = recs.filter(priority=AIRecommendation.Priority.CRITICAL).first()
        priority_actions = recs.order_by('-confidence_score')[:4]

        payload = {
            'store': store,
            'health': compute_health_score(store),
            'kpis': build_kpis(store),
            'ticker': build_ticker(store),
            'insight_banner': insight_banner,
            'priority_actions': priority_actions,
            'upcoming_forecast': upcoming_forecasts(store),
            'stock_alerts': build_stock_alerts(store),
            'top_movers': build_top_movers(store),
            'recent_activity': build_recent_activity(store),
            'debt_summary': debt_summary,
            'generated_at': timezone.now()
        }

        serializer = DashboardSerializer(payload, context={'request': request, 'store': store})
        return Response(serializer.data, status=status.HTTP_200_OK)


class WhatIfSimulatorView(views.APIView):
    """
    POST /api/simulator/what-if/
    Projects revenue/profit over the next PROJECTION_DAYS under a proposed
    price/quantity/ad-spend change, grounded in the product's (or, if no
    product is given, the store's velocity-weighted average) real recent
    sales velocity — not a fixed baseline. Still a simple linear model, not
    a trained one; see `assumptions` in the response for exactly what it
    does and doesn't account for.
    """
    permission_classes = [permissions.IsAuthenticated, IsStoreMember]
    PROJECTION_DAYS = 7

    @extend_schema(request=WhatIfInputSerializer, responses=WhatIfOutputSerializer)
    def post(self, request):
        store = getattr(request, 'store')
        input_serializer = WhatIfInputSerializer(data=request.data, context={'store': store})
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        price_change = data.get('price_change') or Decimal('0.00')
        quantity_change = data.get('quantity_change') or 0
        ad_spend = data.get('ad_spend') or Decimal('0.00')
        product_id = data.get('product_id')

        assumptions = [
            "Linear model: quantity_change is added directly to daily units sold "
            "(no price-elasticity curve — a price change doesn't itself move volume).",
            f"Projected forward {self.PROJECTION_DAYS} days from current sales velocity.",
        ]
        if data.get('staff_count'):
            assumptions.append("staff_count is not yet factored into the projection.")

        product = None
        if product_id:
            product = store.products.get(id=product_id)
            baseline_velocity = product_sales_velocity(product)
            baseline_price = product.selling_price
            baseline_cost = product.cost_price
            assumptions.append(
                f"Based on {product.name}'s trailing {VELOCITY_WINDOW_DAYS}-day sales velocity "
                f"({baseline_velocity:.2f} units/day)."
            )
        else:
            products = list(store.products.filter(is_active=True))
            velocities = {p.id: product_sales_velocity(p) for p in products}
            total_velocity = sum(velocities.values())
            baseline_velocity = total_velocity
            if total_velocity > 0:
                weight = Decimal(str(total_velocity))
                baseline_price = sum(
                    (p.selling_price * Decimal(str(velocities[p.id])) for p in products), Decimal('0.00')
                ) / weight
                baseline_cost = sum(
                    (p.cost_price * Decimal(str(velocities[p.id])) for p in products), Decimal('0.00')
                ) / weight
            else:
                baseline_price = Decimal('0.00')
                baseline_cost = Decimal('0.00')
            assumptions.append(
                "No product specified — using a sales-velocity-weighted average price/cost "
                "across all active products."
            )

        new_price = baseline_price + price_change
        new_daily_velocity = max(baseline_velocity + quantity_change, 0)

        baseline_daily_revenue = baseline_price * Decimal(str(round(baseline_velocity, 2)))
        projected_daily_revenue = new_price * Decimal(str(round(new_daily_velocity, 2)))
        projected_daily_profit = (new_price - baseline_cost) * Decimal(str(round(new_daily_velocity, 2)))

        projected_revenue = projected_daily_revenue * self.PROJECTION_DAYS
        projected_profit = (projected_daily_profit * self.PROJECTION_DAYS) - ad_spend
        baseline_revenue = baseline_daily_revenue * self.PROJECTION_DAYS
        delta_vs_baseline = projected_revenue - baseline_revenue

        if product is not None:
            projected_units_needed = new_daily_velocity * self.PROJECTION_DAYS
            if product.current_stock < projected_units_needed:
                stockout_risk = 'high'
            elif product.current_stock < projected_units_needed * 1.5:
                stockout_risk = 'medium'
            else:
                stockout_risk = 'low'
        else:
            # No single product to check real stock against — a coarse signal only.
            stockout_risk = 'medium' if quantity_change > 0 else 'low'
            assumptions.append(
                "Stockout risk is a rough store-level signal — pick a specific product for a precise read."
            )

        output_data = {
            'projected_revenue': projected_revenue.quantize(Decimal('0.01')),
            'projected_profit': projected_profit.quantize(Decimal('0.01')),
            'delta_vs_baseline': delta_vs_baseline.quantize(Decimal('0.01')),
            'stockout_risk': stockout_risk,
            'confidence': 0.65,  # rule-based linear model, not a trained one — kept deliberately modest
            'assumptions': assumptions,
        }

        output_serializer = WhatIfOutputSerializer(output_data)
        return Response(output_serializer.data, status=status.HTTP_200_OK)


class SalesTrendView(views.APIView):
    """
    GET /api/analytics/sales-trend/?days=30
    Daily revenue series backing the Sales Trends chart, with simple
    rule-based outlier flags (see build_sales_trend's docstring).
    """
    permission_classes = [permissions.IsAuthenticated, IsStoreMember]

    @extend_schema(responses=SalesTrendResponseSerializer)
    def get(self, request):
        store = getattr(request, 'store')
        try:
            days = int(request.query_params.get('days', 30))
        except ValueError:
            days = 30
        days = max(1, min(days, 90))
        return Response({'days': days, 'series': build_sales_trend(store, days=days)})