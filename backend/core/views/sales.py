"""
iPace SME Business Co-Pilot — Sales (checkout, receipts, voids)
"""
from rest_framework import decorators, status
from rest_framework.response import Response

from .base import TenantScopedViewSet, require_roles
from .resources import _uuid_param
from ..models import Sale, StoreMembership
from ..serializers import SaleSerializer, SaleCreateSerializer, VoidSaleSerializer
from ..services.formatting import resolve_period
from ..services.sales import create_sale, void_sale

Role = StoreMembership.Role


class SaleViewSet(TenantScopedViewSet):
    """
    POST /api/sales/            checkout a whole cart (stock, debt, customer stats handled)
    GET  /api/sales/            receipts (?date_from=&date_to=&status=&payment_method=&customer=)
    GET  /api/sales/{id}/       one receipt with its lines
    POST /api/sales/{id}/void/  cancel a sale — restocks and removes it from all totals
    Receipts are never edited or deleted; voiding is the audit-safe way to undo.
    """
    queryset = (
        Sale.objects.select_related('customer', 'cashier')
        .prefetch_related('lines__product', 'debts')
        .all()
    )
    serializer_class = SaleSerializer
    http_method_names = ['get', 'post', 'head', 'options']
    search_fields = ['receipt_number', 'customer__name']
    ordering_fields = ['sold_at', 'total_amount', 'receipt_seq']
    # Cashiers ring up sales; only owner/manager may void (checked in `void`).
    write_roles = {Role.OWNER, Role.MANAGER, Role.CASHIER}

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        raw_from, raw_to = params.get('date_from'), params.get('date_to')
        if raw_from or raw_to:
            _, _, start, end = resolve_period(self.get_store(), raw_from, raw_to)
            qs = qs.filter(sold_at__gte=start, sold_at__lt=end)
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('payment_method'):
            qs = qs.filter(payment_method=params['payment_method'])
        if params.get('customer'):
            qs = qs.filter(customer_id=_uuid_param(params['customer'], 'customer'))
        return qs

    def create(self, request, *args, **kwargs):
        store = self.get_store()
        serializer = SaleCreateSerializer(data=request.data, context={'store': store})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        sale = create_sale(
            store=store,
            user=request.user,
            items=[
                {
                    'product_id': item['product'],
                    'quantity': item['quantity'],
                    'unit_price': item.get('unit_price'),
                }
                for item in data['items']
            ],
            customer=data.get('customer'),
            payment_method=data['payment_method'],
            amount_paid=data.get('amount_paid'),
            due_date=data.get('due_date'),
            notes=data.get('notes', ''),
        )
        sale = self.get_queryset().get(pk=sale.pk)
        return Response(
            SaleSerializer(sale, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @decorators.action(detail=True, methods=['post'], url_path='void', url_name='void')
    def void(self, request, pk=None):
        require_roles(request, {Role.OWNER, Role.MANAGER})
        sale = self.get_object()
        serializer = VoidSaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        void_sale(sale, request.user, serializer.validated_data.get('reason', ''))
        sale = self.get_queryset().get(pk=sale.pk)
        return Response(SaleSerializer(sale, context=self.get_serializer_context()).data)
