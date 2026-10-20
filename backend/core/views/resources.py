"""
iPace SME Business Co-Pilot — Resource ViewSets
=================================================
"""

import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status, decorators, permissions
from rest_framework.response import Response

from .base import TenantScopedViewSet, IsStoreMember, require_roles
from ..models import (
    Supplier, Product, ProductSupplier, Customer, CustomerDebt,
    TransactionLog, PurchaseOrder, AIRecommendation, AIActionLog, StoreMembership,
    StockMovement, Expense,
)
from ..services import debts as debts_service
from ..services import purchasing
from ..services.analytics import store_inventory_snapshot, bulk_velocity_map
from ..services.errors import BusinessRuleError
from ..services.formatting import local_now, parse_date
from ..services.inventory import adjust_stock, apply_stock_change
from ..services.pos_upload import process_pos_upload
from ..serializers import (
    SupplierSerializer, ProductSerializer, ProductSupplierSerializer,
    CustomerSerializer, CustomerDebtSerializer, TransactionLogSerializer,
    PurchaseOrderSerializer, AIRecommendationSerializer,
    ResolveRecommendationSerializer, DispatchPurchaseOrderSerializer,
    SendDebtReminderSerializer, POSUploadSerializer,
    AdjustStockSerializer, StockMovementSerializer, ExpenseSerializer,
    RecordDebtPaymentSerializer, DebtPaymentSerializer,
    ConfirmPurchaseSerializer, ReceivePurchaseSerializer,
)

Role = StoreMembership.Role
FINANCE_ROLES = {Role.OWNER, Role.MANAGER, Role.ACCOUNTANT}


def _uuid_param(raw, name):
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise BusinessRuleError(f"'{name}' must be a valid id.")


class SupplierViewSet(TenantScopedViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    search_fields = ['name', 'contact_person', 'phone_number']
    ordering_fields = ['name', 'reliability_score', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('include_inactive') != 'true':
            qs = qs.filter(is_active=True)
        return qs

    def perform_destroy(self, instance):
        # Suppliers are referenced by purchase orders (PROTECT) — archive, never hard-delete.
        instance.soft_delete()


class ProductViewSet(TenantScopedViewSet):
    queryset = Product.objects.prefetch_related('supplier_links__supplier').all()
    serializer_class = ProductSerializer
    search_fields = ['name', 'sku', 'category']
    ordering_fields = ['name', 'current_stock', 'selling_price', 'created_at']
    # Catalog changes (create/edit/delete/adjust stock) are OWNER + MANAGER —
    # the HasWriteRole default. Cashiers can read the catalog and sell from it.

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('include_inactive') != 'true':
            qs = qs.filter(is_active=True)
        category = params.get('category')
        if category:
            qs = qs.filter(category=category)
        status_param = params.get('status')
        if status_param in ('critical', 'low', 'healthy'):
            snapshot = store_inventory_snapshot(self.get_store())
            matching_ids = [s['product'].id for s in snapshot if s['tier'] == status_param]
            qs = qs.filter(id__in=matching_ids)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # One grouped query gives every product's sales velocity.
        context['velocity_map'] = bulk_velocity_map(self.get_store())
        return context

    def perform_destroy(self, instance):
        # Soft delete: history (sales, purchase orders, ledger) keeps pointing at the
        # product. Free the SKU so it can be reused for a new product.
        instance.sku = f"{instance.sku[:80]}~del{uuid.uuid4().hex[:8]}"
        instance.save(update_fields=['sku', 'updated_at'])
        instance.soft_delete()

    @decorators.action(detail=True, methods=['post'], url_path='adjust-stock', url_name='adjust-stock')
    def adjust_stock_action(self, request, pk=None):
        """POST /api/products/{id}/adjust-stock/ — manual correction, written to the stock ledger."""
        product = self.get_object()
        serializer = AdjustStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        movement = adjust_stock(
            product, user=request.user, mode=data['mode'], quantity=data['quantity'],
            reason=data['reason'], note=data.get('note', ''),
        )
        product.refresh_from_db()
        return Response({
            'product': ProductSerializer(product, context=self.get_serializer_context()).data,
            'movement': StockMovementSerializer(movement).data if movement else None,
        })


class StockMovementViewSet(TenantScopedViewSet):
    """GET /api/stock-movements/ — read-only stock ledger (?product=<id>&type=SALE)."""
    queryset = StockMovement.objects.select_related('product', 'created_by').all()
    serializer_class = StockMovementSerializer
    http_method_names = ['get', 'head', 'options']
    ordering_fields = ['created_at']
    search_fields = ['product__name', 'product__sku', 'note']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('product'):
            qs = qs.filter(product_id=_uuid_param(params['product'], 'product'))
        if params.get('type'):
            qs = qs.filter(movement_type=params['type'])
        return qs


class ProductSupplierViewSet(TenantScopedViewSet):
    queryset = ProductSupplier.objects.select_related('product', 'supplier').all()
    serializer_class = ProductSupplierSerializer
    search_fields = ['product__name', 'supplier__name']

    def get_queryset(self):
        # ProductSupplier has no store column; tenancy is via its product.
        return self.queryset.filter(product__store=self.get_store())


class CustomerViewSet(TenantScopedViewSet):
    queryset = Customer.objects.prefetch_related('debts').all()
    serializer_class = CustomerSerializer
    search_fields = ['name', 'phone_number', 'email']
    ordering_fields = ['name', 'total_lifetime_value', 'last_purchase_at', 'created_at']
    # Cashiers need to add customers at checkout — widen beyond the OWNER/MANAGER default.
    write_roles = {Role.OWNER, Role.MANAGER, Role.CASHIER}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('include_inactive') != 'true':
            qs = qs.filter(is_active=True)
        return qs

    def perform_destroy(self, instance):
        if any(not d.is_settled for d in instance.debts.all()):
            raise BusinessRuleError("This customer still owes money. Settle their debts before removing them.")
        # Archive (their sales history stays intact) and free the phone number for reuse.
        instance.is_active = False
        instance.phone_number = f"{instance.phone_number[:10]}~{uuid.uuid4().hex[:8]}"
        instance.save(update_fields=['is_active', 'phone_number', 'updated_at'])


class CustomerDebtViewSet(TenantScopedViewSet):
    queryset = CustomerDebt.objects.select_related('customer', 'sale').all()
    serializer_class = CustomerDebtSerializer
    search_fields = ['customer__name']
    ordering_fields = ['due_date', 'amount', 'created_at']
    write_roles = {Role.OWNER, Role.MANAGER, Role.ACCOUNTANT}
    # Anyone who handles money at the till may record a repayment.
    payment_roles = {Role.OWNER, Role.MANAGER, Role.ACCOUNTANT, Role.CASHIER}

    def get_permissions(self):
        if self.action == 'record_payment':
            return [permissions.IsAuthenticated(), IsStoreMember()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        settled = params.get('settled')
        if settled == 'true':
            qs = qs.filter(is_settled=True)
        elif settled == 'false':
            qs = qs.filter(is_settled=False)
        if params.get('overdue') == 'true':
            qs = qs.filter(is_settled=False, due_date__lt=local_now(self.get_store()).date())
        if params.get('customer'):
            qs = qs.filter(customer_id=_uuid_param(params['customer'], 'customer'))
        return qs

    def perform_destroy(self, instance):
        if instance.payments.exists():
            raise BusinessRuleError("This debt has repayments recorded and cannot be deleted.")
        instance.delete()

    @decorators.action(detail=True, methods=['post'], url_path='record-payment', url_name='record-payment')
    def record_payment(self, request, pk=None):
        """POST /api/debts/{id}/record-payment/ — log a repayment against a debt."""
        require_roles(request, self.payment_roles)
        debt = self.get_object()
        serializer = RecordDebtPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payment, debt = debts_service.record_payment(
            debt, amount=data['amount'], method=data['method'],
            note=data.get('note', ''), user=request.user,
        )
        return Response({
            'payment': DebtPaymentSerializer(payment).data,
            'debt': CustomerDebtSerializer(debt, context=self.get_serializer_context()).data,
        }, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=['get'], url_path='payments', url_name='payments')
    def payments(self, request, pk=None):
        """GET /api/debts/{id}/payments/ — repayment history for one debt."""
        debt = self.get_object()
        rows = debt.payments.select_related('received_by')
        return Response(DebtPaymentSerializer(rows, many=True).data)

    @decorators.action(detail=False, methods=['post'], url_path='send-reminders')
    def send_reminders(self, request):
        """
        POST /api/debts/send-reminders/
        Builds the reminder message + WhatsApp link for each debt and records
        that a reminder was issued. Messages are NOT sent automatically —
        the user opens each link and presses send in WhatsApp.
        """
        store = self.get_store()
        serializer = SendDebtReminderSerializer(data=request.data, context={'store': store})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        debts = list(
            self.get_queryset().filter(id__in=data['debt_ids'], is_settled=False).select_related('customer')
        )
        reminders = debts_service.build_reminders(
            store, debts, tone=data.get('tone', 'friendly'),
            custom_message=data.get('custom_message', ''),
        )
        CustomerDebt.objects.filter(id__in=[d.id for d in debts]).update(
            last_reminder_sent_at=timezone.now(),
            reminder_count=F('reminder_count') + 1,
        )
        return Response({
            'status': 'queued',
            'delivery': 'manual_whatsapp',
            'processed_count': len(debts),
            'reminders': reminders,
        }, status=status.HTTP_202_ACCEPTED)


class TransactionLogViewSet(TenantScopedViewSet):
    """
    Individual sale lines. Create a single manual line here (it deducts stock);
    use /api/sales/ for a full multi-item checkout and /transactions/upload/
    for historical imports. Lines are never edited or deleted — to undo a
    sale, void it (POST /api/sales/{id}/void/) so stock and totals stay correct.
    """
    queryset = TransactionLog.objects.select_related('product', 'customer').all()
    serializer_class = TransactionLogSerializer
    search_fields = ['product__name', 'customer__name', 'external_id']
    ordering_fields = ['transaction_date', 'total_amount']
    http_method_names = ['get', 'post', 'head', 'options']
    # Cashiers record sales day to day.
    write_roles = {Role.OWNER, Role.MANAGER, Role.CASHIER}

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('include_voided') != 'true':
            qs = qs.filter(is_voided=False)
        if params.get('product'):
            qs = qs.filter(product_id=_uuid_param(params['product'], 'product'))
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        product = serializer.validated_data.get('product')
        quantity = serializer.validated_data.get('quantity_sold')
        locked = None
        if product is not None:
            locked = Product.objects.select_for_update().get(pk=product.pk)
            if quantity > locked.current_stock:
                raise BusinessRuleError(
                    f"Only {locked.current_stock} of {locked.name} in stock (you tried to sell {quantity})."
                )
        txn = serializer.save()
        if locked is not None:
            apply_stock_change(
                locked, -txn.quantity_sold, StockMovement.Type.SALE, user=self.request.user,
                note='Manual sale entry', reference_type='transaction', reference_id=txn.id,
            )

    @decorators.action(detail=False, methods=['post'], url_path='upload', url_name='upload')
    def upload_pos_data(self, request):
        """POST /api/transactions/upload/ — parses and (optionally) commits a CSV/XLSX sales export."""
        serializer = POSUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data['file']
        source = serializer.validated_data['source']
        dry_run = serializer.validated_data['dry_run']
        update_stock = serializer.validated_data['update_stock']

        result = process_pos_upload(
            store=self.get_store(),
            uploaded_file=uploaded_file,
            source=source,
            dry_run=dry_run,
            update_stock=update_stock,
            user=request.user,
        )

        return Response({
            'status': 'success' if result.invalid_rows == 0 else 'completed_with_errors',
            'dry_run': dry_run,
            'update_stock': update_stock,
            'rows_parsed': result.rows_parsed,
            'valid_rows': result.valid_rows,
            'invalid_rows': result.invalid_rows,
            'duplicate_rows': result.duplicate_rows,
            'estimated_prices': result.estimated_prices,
            'created_transactions': result.created_transactions,
            'issues': [
                {'row': issue.row_number, 'reason': issue.reason}
                for issue in result.issues[:50]  # cap payload size on very messy files
            ],
        }, status=status.HTTP_200_OK)


class PurchaseOrderViewSet(TenantScopedViewSet):
    queryset = PurchaseOrder.objects.prefetch_related('items__product').select_related('supplier', 'store').all()
    serializer_class = PurchaseOrderSerializer
    search_fields = ['supplier__name']
    ordering_fields = ['created_at', 'total_cost']

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def perform_destroy(self, instance):
        if instance.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.CANCELLED):
            raise BusinessRuleError("Only draft or cancelled purchase orders can be deleted.")
        instance.delete()

    def _respond(self, po):
        po = self.get_queryset().get(pk=po.pk)
        return Response(self.get_serializer(po).data)

    @decorators.action(detail=True, methods=['post'], url_path='send', url_name='send')
    def send(self, request, pk=None):
        """POST /api/purchase-orders/{id}/send/ — mark the order as sent to the supplier."""
        return self._respond(purchasing.mark_sent(self.get_object()))

    @decorators.action(detail=True, methods=['post'], url_path='confirm', url_name='confirm')
    def confirm(self, request, pk=None):
        """POST /api/purchase-orders/{id}/confirm/ — the supplier confirmed the order."""
        serializer = ConfirmPurchaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        po = purchasing.confirm(self.get_object(), serializer.validated_data.get('expected_delivery'))
        return self._respond(po)

    @decorators.action(detail=True, methods=['post'], url_path='receive', url_name='receive')
    def receive(self, request, pk=None):
        """POST /api/purchase-orders/{id}/receive/ — goods arrived; adds the units to stock."""
        serializer = ReceivePurchaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        po = purchasing.receive(
            self.get_object(), request.user,
            received=[{'item_id': i['item_id'], 'quantity': i['quantity']} for i in data['items']],
            update_cost_price=data['update_cost_price'],
        )
        return self._respond(po)

    @decorators.action(detail=True, methods=['post'], url_path='cancel', url_name='cancel')
    def cancel(self, request, pk=None):
        """POST /api/purchase-orders/{id}/cancel/"""
        return self._respond(purchasing.cancel(self.get_object()))

    @decorators.action(detail=False, methods=['post'], url_path='dispatch', url_name='dispatch')
    def dispatch_po(self, request):
        """POST /api/purchase-orders/dispatch/"""
        store = self.get_store()
        serializer = DispatchPurchaseOrderSerializer(data=request.data, context={'store': store})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        supplier = Supplier.objects.get(id=data['supplier_id'], store=store)
        product_ids = [uuid.UUID(str(item['product_id'])) for item in data['items']]
        products_by_id = {p.id: p for p in Product.objects.filter(id__in=product_ids, store=store)}

        # DispatchPurchaseOrderSerializer validates a lightweight shape
        # ({product_id, quantity}) meant for a "reorder in one click" flow —
        # translate it into what PurchaseOrderItemSerializer actually needs
        # (product, quantity_ordered, unit_cost), pricing each line at the
        # product's current cost price since this flow doesn't collect one.
        po_items, message_lines = [], []
        for item in data['items']:
            product = products_by_id[uuid.UUID(str(item['product_id']))]
            qty = int(item['quantity'])
            po_items.append({
                'product': product.id, 'quantity_ordered': qty, 'unit_cost': product.cost_price,
            })
            message_lines.append(f"- {product.name}: {qty} units")

        whatsapp_message = data.get('custom_message') or (
            f"Hello {supplier.name},\nPlease process this purchase order:\n" + "\n".join(message_lines)
        )

        po_serializer = PurchaseOrderSerializer(
            data={'supplier': str(supplier.id), 'items': po_items, 'whatsapp_message': whatsapp_message},
            context={'store': store, 'request': request}
        )
        po_serializer.is_valid(raise_exception=True)
        po = po_serializer.save()

        recommendation_id = data.get('recommendation_id')
        if recommendation_id:
            AIRecommendation.objects.filter(
                id=recommendation_id, store=store, is_resolved=False
            ).update(is_resolved=True, resolved_at=timezone.now())

        return Response(PurchaseOrderSerializer(po).data, status=status.HTTP_201_CREATED)


class ExpenseViewSet(TenantScopedViewSet):
    """Business expenses — visible to and editable by owner, manager and accountant only."""
    queryset = Expense.objects.select_related('recorded_by').all()
    serializer_class = ExpenseSerializer
    search_fields = ['description']
    ordering_fields = ['expense_date', 'amount', 'created_at']
    read_roles = FINANCE_ROLES
    write_roles = FINANCE_ROLES

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        date_from, date_to = parse_date(params.get('date_from'), 'date_from'), parse_date(params.get('date_to'), 'date_to')
        if date_from:
            qs = qs.filter(expense_date__gte=date_from)
        if date_to:
            qs = qs.filter(expense_date__lte=date_to)
        if params.get('category'):
            qs = qs.filter(category=params['category'])
        return qs


class AIRecommendationViewSet(TenantScopedViewSet):
    queryset = AIRecommendation.objects.select_related('product', 'supplier', 'customer').all()
    serializer_class = AIRecommendationSerializer
    search_fields = ['title', 'category']
    ordering_fields = ['created_at', 'confidence_score', 'priority']
    # Recommendations are system-generated — catalog CRUD stays OWNER/MANAGER
    # (the HasWriteRole default), but any active member can act on one from
    # their own feed, so `resolve` gets its own, looser permission below.

    def get_permissions(self):
        if self.action == 'resolve_recommendation':
            return [permissions.IsAuthenticated(), IsStoreMember()]
        return super().get_permissions()

    @decorators.action(detail=True, methods=['post'], url_path='resolve', url_name='resolve')
    def resolve_recommendation(self, request, pk=None):
        """
        POST /api/recommendations/{id}/resolve/
        Logs the action taken (accepted/dismissed/modified/snoozed) via
        AIActionLog, and only marks the recommendation resolved for the
        action types that actually represent closure.
        """
        recommendation = self.get_object()
        serializer = ResolveRecommendationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data['action']

        AIActionLog.objects.create(
            store=self.get_store(),
            recommendation=recommendation,
            user=request.user,
            action_taken=action,
            feedback=serializer.validated_data.get('feedback', ''),
            metadata=serializer.validated_data.get('metadata', {}),
        )

        closing_actions = {
            AIActionLog.Action.ACCEPTED,
            AIActionLog.Action.DISMISSED,
            AIActionLog.Action.MODIFIED,
            AIActionLog.Action.AUTO_EXECUTED,
        }
        if action in closing_actions and not recommendation.is_resolved:
            recommendation.is_resolved = True
            recommendation.resolved_at = timezone.now()
            recommendation.save(update_fields=['is_resolved', 'resolved_at', 'updated_at'])

        return Response(
            {'status': action.lower(), 'id': recommendation.id, 'is_resolved': recommendation.is_resolved},
            status=status.HTTP_200_OK,
        )
