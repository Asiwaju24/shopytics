"""
iPace SME Business Co-Pilot — DRF Serializers
================================================
Production-grade serializers for the multi-tenant SME platform.

Design principles:
- Tenant Isolation: `store` is ALWAYS derived from request context, never
  trusted from the payload. Every FK is validated against the tenant boundary.
- Read/Write Separation: rich nested reads for dashboards, flat ID-based
  writes for actions.
- Explicit Validation: `full_clean()` is invoked on model-backed writes so
  model-level `clean()` methods run. Field validators are enforced.
- Computed Fields Are Read-Only: margin, totals, scores, and audit fields
  are never accepted from clients.
- Composite Endpoints: single-call dashboard serializers avoid N+1 chatter.
"""

from decimal import Decimal
import uuid

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (
    User, Store, StoreMembership, Supplier, Product, ProductSupplier,
    Customer, CustomerDebt, TransactionLog, DailySalesSummary,
    PurchaseOrder, PurchaseOrderItem, StructuralBreakLog,
    DemandForecast, AIRecommendation, AIActionLog,
    Sale, StockMovement, DebtPayment, Expense,
)
from .services.formatting import local_now
from .services.inventory import adjust_stock


# ═══════════════════════════════════════════════════════════════════════
# 0. BASE CLASSES
# ═══════════════════════════════════════════════════════════════════════

class TenantScopedSerializer(serializers.ModelSerializer):
    """
    Base serializer for every store-scoped model.

    Guarantees:
      - `store` is injected from `self.context['store']` on create.
      - Any tenant FK declared in `TENANT_FK_FIELDS` is validated to belong
        to the same store as the request context.
      - Model `clean()` is executed on write, catching cross-field issues
        that field validators cannot.
      - Requires an authenticated request with a resolved `store` context.
    """

    store = serializers.PrimaryKeyRelatedField(read_only=True)
    TENANT_FK_FIELDS: tuple[str, ...] = ()

    # ---- helpers -----------------------------------------------------

    def _get_request(self):
        request = self.context.get('request')
        if not request or not getattr(request.user, 'is_authenticated', False):
            raise serializers.ValidationError("Authentication required.")
        return request

    def _get_store(self):
        store = self.context.get('store')
        if store is None:
            raise serializers.ValidationError(
                "Store context missing — check view's get_serializer_context()."
            )
        return store

    # ---- validation --------------------------------------------------

    def validate(self, attrs):
        store = self._get_store()

        for field in self.TENANT_FK_FIELDS:
            value = attrs.get(field, getattr(self.instance, field, None))
            if value is None:
                continue
            if getattr(value, 'store_id', None) != store.id:
                raise serializers.ValidationError({
                    field: "Referenced object does not belong to this store."
                })

        return attrs

    # ---- writes ------------------------------------------------------

    def create(self, validated_data):
        validated_data['store'] = self._get_store()
        instance = self.Meta.model(**validated_data)
        instance.full_clean()
        instance.save()
        return instance

    def update(self, instance, validated_data):
        validated_data.pop('store', None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.full_clean()
        instance.save()
        return instance


# ═══════════════════════════════════════════════════════════════════════
# 1. USER & TENANT SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class PublicUserSerializer(serializers.ModelSerializer):
    """Minimal surface — safe to expose anywhere."""
    class Meta:
        model = User
        fields = ['id', 'full_name', 'profile_picture_url']
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    """Full self-profile — only for the authenticated user's own record."""
    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'phone_number', 'full_name',
            'profile_picture_url', 'is_verified', 'last_active_at', 'created_at',
        ]
        read_only_fields = [
            'id', 'is_verified', 'last_active_at', 'created_at',
        ]


class RegisterSerializer(serializers.ModelSerializer):
    """Payload for `POST /api/auth/register/`."""
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'password',
            'phone_number', 'full_name',
        ]
        read_only_fields = ['id']

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("That username is already taken.")
        return value

    def validate_phone_number(self, value):
        # phone_number is unique-or-NULL: store blanks as NULL so two users
        # without a phone number don't collide on ''.
        return value or None

    def validate_email(self, value):
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with that email already exists.")
        return value

    def validate(self, attrs):
        # Run Django's configured AUTH_PASSWORD_VALIDATORS (min length,
        # common-password check, similarity to username/email, etc.).
        # Built against a transient, unsaved User so
        # UserAttributeSimilarityValidator can compare the password
        # against the username/email being registered, not an existing row.
        transient_user = User(
            username=attrs.get('username', ''),
            email=attrs.get('email', ''),
            full_name=attrs.get('full_name', ''),
        )
        try:
            validate_password(attrs['password'], user=transient_user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user


class UpdateProfileSerializer(serializers.ModelSerializer):
    """Payload for `PATCH /api/auth/me/` — partial updates to your own profile."""

    class Meta:
        model = User
        fields = ['username', 'email', 'phone_number', 'full_name', 'profile_picture_url']

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("That username is already taken.")
        return value

    def validate_phone_number(self, value):
        return value or None

    def validate_email(self, value):
        if value and User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("An account with that email already exists.")
        return value


class ChangePasswordSerializer(serializers.Serializer):
    """Payload for `POST /api/auth/change-password/`."""
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        user = self.context['request'].user
        try:
            validate_password(value, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def save(self):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password'])
        return user


class LogoutSerializer(serializers.Serializer):
    """Payload for `POST /api/auth/logout/`."""
    refresh = serializers.CharField(help_text="The refresh token to blacklist.")


class StoreSerializer(serializers.ModelSerializer):
    """
    Store write access is restricted: owner is auto-assigned, and the
    soft-delete flags are managed through a dedicated endpoint, not PATCH.
    """
    owner_name = serializers.ReadOnlyField(source='owner.full_name')
    owner_id = serializers.ReadOnlyField(source='owner.id')

    class Meta:
        model = Store
        fields = [
            'id', 'owner_id', 'owner_name', 'name', 'business_type',
            'currency', 'timezone', 'whatsapp_number', 'address', 'city',
            'country', 'daily_revenue_target', 'tax_rate_percent', 'receipt_footer',
            'enable_auto_pilot', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'owner_id', 'owner_name', 'is_active', 'created_at', 'updated_at',
        ]

    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication required.")
        validated_data['owner'] = request.user
        store = Store.objects.create(**validated_data)
        StoreMembership.objects.create(
            user=request.user,
            store=store,
            role=StoreMembership.Role.OWNER,
        )
        return store


class StoreMembershipSerializer(serializers.ModelSerializer):
    """
    Membership creation is restricted to existing OWNERs of the same store.
    Enforced in the view layer via permission classes; the serializer
    guarantees `store` is always request-scoped.
    """
    user_email = serializers.ReadOnlyField(source='user.email')
    user_name = serializers.ReadOnlyField(source='user.full_name')
    username = serializers.ReadOnlyField(source='user.username')
    store = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = StoreMembership
        fields = [
            'id', 'user', 'username', 'user_email', 'user_name', 'store',
            'role', 'is_active', 'joined_at',
        ]
        read_only_fields = ['id', 'store', 'joined_at']

    def validate(self, attrs):
        store = self.context.get('store')
        if store is None:
            raise serializers.ValidationError("Store context missing.")

        user = attrs.get('user') or getattr(self.instance, 'user', None)
        if user is None:
            raise serializers.ValidationError({'user': "Required."})

        role = attrs.get('role')
        was_owner = bool(self.instance and self.instance.role == StoreMembership.Role.OWNER)
        if role == StoreMembership.Role.OWNER and not was_owner:
            raise serializers.ValidationError({'role': "Ownership cannot be assigned here."})

        qs = StoreMembership.objects.filter(store=store, user=user)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                "User already has a membership for this store."
            )
        return attrs

    def create(self, validated_data):
        validated_data['store'] = self.context['store']
        return StoreMembership.objects.create(**validated_data)


class AddStaffSerializer(serializers.Serializer):
    """Payload for `POST /api/memberships/add-staff/`."""
    username = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(
        choices=[c for c in StoreMembership.Role.choices if c[0] != StoreMembership.Role.OWNER]
    )
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=20)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)


class MeResponseSerializer(serializers.Serializer):
    """Response shape for `GET /api/auth/me/` (schema/docs only)."""
    user = UserSerializer()
    memberships = StoreMembershipSerializer(many=True)


# ═══════════════════════════════════════════════════════════════════════
# 2. SUPPLIER SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class SupplierSerializer(TenantScopedSerializer):
    class Meta:
        model = Supplier
        fields = [
            'id', 'store', 'name', 'contact_person', 'whatsapp_number',
            'phone_number', 'email', 'address', 'lead_time_days',
            'reliability_score', 'quality_score', 'notes',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'reliability_score', 'quality_score',
            'created_at', 'updated_at',
        ]

    def validate_whatsapp_number(self, value):
        if value:
            digits = ''.join(ch for ch in value if ch.isdigit())
            if len(digits) < 10:
                raise serializers.ValidationError("WhatsApp number looks invalid.")
        return value


# ═══════════════════════════════════════════════════════════════════════
# 3. INVENTORY & PRODUCT CATALOG SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class ProductSupplierSerializer(TenantScopedSerializer):
    supplier_name = serializers.ReadOnlyField(source='supplier.name')
    product_name = serializers.ReadOnlyField(source='product.name')

    TENANT_FK_FIELDS = ('product', 'supplier')

    def create(self, validated_data):
        # ProductSupplier has no `store` column — tenancy comes via product/supplier.
        validated_data.pop('store', None)
        instance = ProductSupplier(**validated_data)
        instance.full_clean()
        instance.save()
        return instance

    class Meta:
        model = ProductSupplier
        fields = [
            'id', 'product', 'product_name', 'supplier', 'supplier_name',
            'supplier_sku', 'last_cost_price', 'minimum_order_qty',
            'is_preferred', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'store', 'created_at', 'updated_at']

    def validate(self, attrs):
        attrs = super().validate(attrs)

        # Only one preferred supplier per product
        is_preferred = attrs.get(
            'is_preferred', getattr(self.instance, 'is_preferred', False)
        )
        product = attrs.get('product') or getattr(self.instance, 'product', None)
        if is_preferred and product is not None:
            qs = ProductSupplier.objects.filter(
                product=product, is_preferred=True
            )
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    'is_preferred': "Another preferred supplier is already set."
                })
        return attrs


class ProductSerializer(TenantScopedSerializer):
    margin_percent = serializers.ReadOnlyField()
    supplier_links = ProductSupplierSerializer(many=True, read_only=True)
    # Computed from real sales history (see core.services.analytics) — not
    # stored fields. Cached per-instance within one serialization pass so
    # listing N products costs one velocity query per product, not three.
    daily_velocity = serializers.SerializerMethodField()
    days_of_stock_remaining = serializers.SerializerMethodField()
    stock_tier = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'store', 'sku', 'name', 'category', 'description',
            'cost_price', 'selling_price', 'margin_percent', 'current_stock',
            'reorder_point', 'safety_stock', 'unit_of_measure', 'image_url',
            'supplier_links', 'daily_velocity', 'days_of_stock_remaining', 'stock_tier',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'margin_percent', 'created_at', 'updated_at',
        ]

    def _velocity(self, obj):
        if not hasattr(obj, '_cached_velocity'):
            velocity_map = self.context.get('velocity_map')
            if velocity_map is not None:
                obj._cached_velocity = velocity_map.get(obj.id, 0.0)
            else:
                from .services.analytics import product_sales_velocity
                obj._cached_velocity = product_sales_velocity(obj)
        return obj._cached_velocity

    def _user(self):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        return user if getattr(user, 'is_authenticated', False) else None

    @transaction.atomic
    def create(self, validated_data):
        product = super().create(validated_data)
        if product.current_stock > 0:
            StockMovement.objects.create(
                store=product.store, product=product,
                movement_type=StockMovement.Type.OPENING,
                quantity_change=product.current_stock,
                stock_before=0, stock_after=product.current_stock,
                reference_type='manual', note='Opening stock', created_by=self._user(),
            )
        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        # Stock edits go through the ledger so history always explains the number.
        new_stock = validated_data.pop('current_stock', None)
        instance = super().update(instance, validated_data)
        if new_stock is not None and new_stock != instance.current_stock:
            adjust_stock(
                instance, user=self._user(), mode='set', quantity=new_stock,
                reason=StockMovement.Type.COUNT, note='Edited on product form',
            )
        return instance

    def get_daily_velocity(self, obj):
        return round(self._velocity(obj), 2)

    def get_days_of_stock_remaining(self, obj):
        from .services.analytics import product_days_remaining
        return product_days_remaining(obj, velocity=self._velocity(obj))

    def get_stock_tier(self, obj):
        from .services.analytics import product_days_remaining, product_stock_tier
        velocity = self._velocity(obj)
        days = product_days_remaining(obj, velocity=velocity)
        return product_stock_tier(obj, velocity=velocity, days_remaining=days)

    def validate(self, attrs):
        attrs = super().validate(attrs)

        selling = attrs.get(
            'selling_price', getattr(self.instance, 'selling_price', None)
        )
        cost = attrs.get('cost_price', getattr(self.instance, 'cost_price', Decimal('0')))
        if selling is not None and cost is not None and selling < cost:
            raise serializers.ValidationError({
                'selling_price': "Selling price cannot be below cost price."
            })
        return attrs


# ═══════════════════════════════════════════════════════════════════════
# 4. CUSTOMER & DEBT SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class CustomerSerializer(TenantScopedSerializer):
    outstanding_debt = serializers.SerializerMethodField()

    def get_outstanding_debt(self, obj):
        total = sum(
            (d.outstanding_amount for d in obj.debts.all() if not d.is_settled),
            Decimal('0.00'),
        )
        return str(total)

    class Meta:
        model = Customer
        fields = [
            'id', 'store', 'name', 'phone_number', 'whatsapp_opt_in',
            'email', 'notes', 'total_lifetime_value', 'total_orders',
            'outstanding_debt', 'last_purchase_at', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'total_lifetime_value', 'total_orders',
            'last_purchase_at', 'created_at', 'updated_at',
        ]


class CustomerDebtSerializer(TenantScopedSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.name')
    customer_phone = serializers.ReadOnlyField(source='customer.phone_number')
    outstanding_amount = serializers.ReadOnlyField()
    days_overdue = serializers.ReadOnlyField()
    sale_receipt = serializers.SerializerMethodField()

    TENANT_FK_FIELDS = ('customer',)

    def get_sale_receipt(self, obj):
        return obj.sale.receipt_number if obj.sale_id else None

    class Meta:
        model = CustomerDebt
        fields = [
            'id', 'store', 'customer', 'customer_name', 'customer_phone', 'sale_receipt',
            'amount', 'amount_paid', 'outstanding_amount',
            'due_date', 'days_overdue', 'notes',
            'is_settled', 'settled_at',
            'last_reminder_sent_at', 'reminder_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'amount_paid', 'outstanding_amount', 'days_overdue',
            'is_settled', 'settled_at',
            'last_reminder_sent_at', 'reminder_count',
            'created_at', 'updated_at',
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)

        amount = attrs.get('amount', getattr(self.instance, 'amount', None))
        amount_paid = attrs.get(
            'amount_paid', getattr(self.instance, 'amount_paid', Decimal('0.00'))
        )

        if amount is not None and amount_paid is not None and amount_paid > amount:
            raise serializers.ValidationError({
                'amount_paid': "Amount paid cannot exceed total debt."
            })

        # Derive settlement state — never trust client
        if amount is not None and amount_paid is not None and amount_paid >= amount:
            attrs['is_settled'] = True
            attrs['settled_at'] = timezone.now()
        else:
            attrs['is_settled'] = False
            attrs['settled_at'] = None
        return attrs


# ═══════════════════════════════════════════════════════════════════════
# 5. TRANSACTION & SALES SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class TransactionLogSerializer(TenantScopedSerializer):
    product_name = serializers.ReadOnlyField(source='product.name')
    customer_name = serializers.ReadOnlyField(source='customer.name')

    TENANT_FK_FIELDS = ('product', 'customer')

    class Meta:
        model = TransactionLog
        fields = [
            'id', 'store', 'product', 'product_name', 'customer', 'customer_name',
            'quantity_sold', 'unit_price', 'unit_cost', 'total_amount',
            'currency', 'transaction_date', 'source', 'external_id', 'notes',
            'sale', 'is_voided', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'total_amount', 'unit_cost', 'currency',
            'sale', 'is_voided', 'created_at', 'updated_at',
        ]
        # `external_id` backs a conditional UniqueConstraint (unique only
        # when non-empty — see TransactionLog.Meta.constraints). DRF's
        # ModelSerializer auto-detects that constraint and, by default,
        # forces every field involved to be `required=True` so it can
        # check the combination — even though the model field itself is
        # `blank=True` and most transactions (manual entries, in
        # particular) legitimately have no external_id at all. Without
        # this override, POSTing a transaction without one is rejected
        # with a misleading "This field is required."
        extra_kwargs = {
            'external_id': {'required': False},
        }

    def create(self, validated_data):
        validated_data['store'] = self._get_store()
        instance = TransactionLog(**validated_data)

        # Snapshot unit cost and store currency if missing
        if not instance.unit_cost and instance.product_id:
            instance.unit_cost = instance.product.cost_price
        if not instance.currency and instance.store_id:
            instance.currency = instance.store.currency

        if instance.quantity_sold is not None and instance.unit_price is not None:
            instance.total_amount = Decimal(str(instance.quantity_sold)) * instance.unit_price

        instance.full_clean()
        instance.save()
        return instance

    def update(self, instance, validated_data):
        validated_data.pop('store', None)
        for key, value in validated_data.items():
            setattr(instance, key, value)

        if instance.quantity_sold is not None and instance.unit_price is not None:
            instance.total_amount = Decimal(str(instance.quantity_sold)) * instance.unit_price

        instance.full_clean()
        instance.save()
        return instance


class DailySalesSummarySerializer(serializers.ModelSerializer):
    """Read-only — populated by the nightly aggregator, never by clients."""
    gross_profit = serializers.ReadOnlyField()

    class Meta:
        model = DailySalesSummary
        fields = [
            'id', 'store', 'product', 'product_name_snapshot', 'date',
            'total_quantity_sold', 'total_revenue', 'total_cost', 'gross_profit',
            'transaction_count', 'average_unit_price',
            'seasonality_flag', 'is_weekend',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


# ═══════════════════════════════════════════════════════════════════════
# 6. PURCHASE ORDER SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class PurchaseOrderItemSerializer(TenantScopedSerializer):
    product_name = serializers.ReadOnlyField(source='product.name')
    line_total = serializers.ReadOnlyField()

    TENANT_FK_FIELDS = ('product',)

    class Meta:
        model = PurchaseOrderItem
        fields = [
            'id', 'purchase_order', 'product', 'product_name',
            'quantity_ordered', 'quantity_received', 'unit_cost',
            'line_total', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'line_total', 'quantity_received', 'created_at', 'updated_at', 'purchase_order'
        ]


class PurchaseOrderSerializer(TenantScopedSerializer):
    """
    Supports nested writes: create a PO with its items in a single request.
    `total_cost` is always recomputed from the items — never trusted.
    """
    items = PurchaseOrderItemSerializer(many=True)
    supplier_name = serializers.ReadOnlyField(source='supplier.name')
    supplier_whatsapp = serializers.SerializerMethodField()
    whatsapp_url = serializers.SerializerMethodField()

    TENANT_FK_FIELDS = ('supplier',)

    def _supplier_number(self, obj):
        return obj.supplier.whatsapp_number or obj.supplier.phone_number

    def get_supplier_whatsapp(self, obj):
        return self._supplier_number(obj)

    def get_whatsapp_url(self, obj):
        from .services.formatting import whatsapp_link
        return whatsapp_link(self._supplier_number(obj), obj.whatsapp_message or '', obj.store.currency)

    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'store', 'supplier', 'supplier_name', 'supplier_whatsapp', 'created_by',
            'status', 'whatsapp_message', 'whatsapp_url',
            'sent_at', 'confirmed_at', 'expected_delivery', 'delivered_at',
            'total_cost', 'items',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'created_by', 'total_cost', 'status',
            'sent_at', 'confirmed_at', 'delivered_at',
            'created_at', 'updated_at',
        ]

    def validate_supplier(self, value):
        store = self.context.get('store')
        if store and value.store_id != store.id:
            raise serializers.ValidationError("Selected supplier does not belong to your store.")
        return value

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        store = self._get_store()

        validated_data['store'] = store
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            validated_data['created_by'] = request.user

        po = PurchaseOrder.objects.create(**validated_data)

        total = Decimal('0.00')
        for item_data in items_data:
            item_data['purchase_order'] = po
            item_data.pop('store', None)
            item = PurchaseOrderItem.objects.create(**item_data)
            total += item.line_total

        po.total_cost = total
        po.save(update_fields=['total_cost', 'updated_at'])
        return po

    @transaction.atomic
    def update(self, instance, validated_data):
        if instance.status != PurchaseOrder.Status.DRAFT:
            raise serializers.ValidationError("Only draft purchase orders can be edited.")
        items_data = validated_data.pop('items', None)
        validated_data.pop('store', None)
        validated_data.pop('created_by', None)

        for key, value in validated_data.items():
            setattr(instance, key, value)

        if items_data is not None:
            instance.items.all().delete()
            total = Decimal('0.00')
            for item_data in items_data:
                item_data['purchase_order'] = instance
                item_data.pop('store', None)
                item = PurchaseOrderItem.objects.create(**item_data)
                total += item.line_total
            instance.total_cost = total

        instance.full_clean()
        instance.save()
        return instance


# ═══════════════════════════════════════════════════════════════════════
# 7. FORECASTING, STRUCTURAL BREAK & AI ENGINE SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class DemandForecastSerializer(serializers.ModelSerializer):
    """Read-only — populated by the forecasting service."""
    product_name = serializers.ReadOnlyField(source='product.name')

    class Meta:
        model = DemandForecast
        fields = [
            'id', 'store', 'product', 'product_name', 'horizon',
            'forecast_date', 'predicted_quantity', 'predicted_revenue',
            'confidence_lower', 'confidence_upper', 'confidence_score',
            'model_version', 'model_metadata', 'generated_at',
        ]
        read_only_fields = fields


class StructuralBreakLogSerializer(serializers.ModelSerializer):
    """Read-only — populated by the statistical break-detection service."""
    product_name = serializers.ReadOnlyField(source='product.name')

    class Meta:
        model = StructuralBreakLog
        fields = [
            'id', 'store', 'product', 'product_name',
            'break_date', 'method', 'p_value', 'test_statistic',
            'pre_break_avg_daily_sales', 'post_break_avg_daily_sales',
            'change_percent', 'notes', 'detected_at',
        ]
        read_only_fields = fields


class AIRecommendationSerializer(serializers.ModelSerializer):
    """
    Read-only for the dashboard. Mutations happen via dedicated action
    endpoints (`/recommendations/{id}/resolve/`) so the AIActionLog is
    always written. Never allow PATCH on `is_resolved`.
    """
    product_name = serializers.ReadOnlyField(source='product.name')
    supplier_name = serializers.ReadOnlyField(source='supplier.name')
    customer_name = serializers.ReadOnlyField(source='customer.name')

    class Meta:
        model = AIRecommendation
        fields = [
            'id', 'store',
            'product', 'product_name',
            'customer', 'customer_name',
            'supplier', 'supplier_name',
            'forecast',
            'priority', 'category', 'title', 'body', 'action_text',
            'suggested_reorder_qty', 'estimated_financial_impact',
            'confidence_score',
            'is_resolved', 'resolved_at', 'expires_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class AIActionLogSerializer(serializers.ModelSerializer):
    recommendation_title = serializers.ReadOnlyField(source='recommendation.title')
    user_name = serializers.ReadOnlyField(source='user.full_name')

    class Meta:
        model = AIActionLog
        fields = [
            'id', 'store', 'recommendation', 'recommendation_title',
            'user', 'user_name', 'action_taken', 'feedback',
            'metadata', 'created_at',
        ]
        read_only_fields = fields


# ═══════════════════════════════════════════════════════════════════════
# 8. ACTION SERIALIZERS (POST-only, non-model)
# ═══════════════════════════════════════════════════════════════════════

class ResolveRecommendationSerializer(serializers.Serializer):
    """Payload for `POST /recommendations/{id}/resolve/`."""
    action = serializers.ChoiceField(choices=AIActionLog.Action.choices)
    feedback = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)


class DispatchPurchaseOrderSerializer(serializers.Serializer):
    """
    Payload for `POST /purchase-orders/dispatch/` — the '1-Click PO' flow.
    Validates that every product and the supplier belong to the store,
    then hands off to the WhatsApp service layer.
    """
    supplier_id = serializers.UUIDField()
    recommendation_id = serializers.UUIDField(required=False, allow_null=True)
    items = serializers.ListField(
        child=serializers.DictField(), allow_empty=False
    )
    custom_message = serializers.CharField(required=False, allow_blank=True)

    def validate_supplier_id(self, value):
        store = self.context.get('store')
        if not Supplier.objects.filter(id=value, store=store, is_active=True).exists():
            raise serializers.ValidationError("Supplier not found in this store.")
        return value

    def validate_recommendation_id(self, value):
        if value in (None, ''):
            return value
        store = self.context.get('store')
        if not AIRecommendation.objects.filter(id=value, store=store).exists():
            raise serializers.ValidationError("Recommendation not found.")
        return value

    def validate_items(self, items):
        store = self.context.get('store')
        raw_product_ids = [item.get('product_id') for item in items]

        if any(pid is None for pid in raw_product_ids):
            raise serializers.ValidationError(
                "Each item must include 'product_id'."
            )

        parsed_uuids = []
        for pid in raw_product_ids:
            try:
                parsed_uuids.append(uuid.UUID(str(pid)))
            except (ValueError, AttributeError):
                raise serializers.ValidationError(f"Invalid UUID: {pid}")

        valid_ids = set(
            Product.objects.filter(
                id__in=parsed_uuids, store=store, is_active=True
            ).values_list('id', flat=True)
        )
        
        missing = [str(pid) for pid in parsed_uuids if pid not in valid_ids]
        if missing:
            raise serializers.ValidationError(
                f"Products not found in store: {', '.join(missing)}"
            )

        for item in items:
            qty = item.get('quantity')
            if qty is None or int(qty) <= 0:
                raise serializers.ValidationError(
                    "Each item must include a positive 'quantity'."
                )
        return items


class SendDebtReminderSerializer(serializers.Serializer):
    """Payload for bulk debt reminders via WhatsApp."""
    debt_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False
    )
    tone = serializers.ChoiceField(
        choices=['friendly', 'firm', 'final'], default='friendly'
    )
    custom_message = serializers.CharField(required=False, allow_blank=True)

    def validate_debt_ids(self, ids):
        store = self.context.get('store')
        count = CustomerDebt.objects.filter(
            id__in=ids, store=store, is_settled=False
        ).count()
        if count != len(ids):
            raise serializers.ValidationError(
                "Some debts are invalid or already settled."
            )
        return ids


class POSUploadSerializer(serializers.Serializer):
    """Payload for `POST /transactions/upload/` — the CSV/Excel cleaner."""
    file = serializers.FileField()
    source = serializers.ChoiceField(choices=TransactionLog.Source.choices)
    dry_run = serializers.BooleanField(
        default=True, help_text="Preview without committing changes"
    )
    update_stock = serializers.BooleanField(
        default=False,
        help_text="Also deduct the imported quantities from current stock (leave off for historical data)",
    )

    def validate_file(self, f):
        if f.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("File exceeds 10MB limit.")
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in ('csv', 'xlsx'):
            raise serializers.ValidationError(
                "Only CSV or XLSX files are accepted (save old .xls files as .xlsx first)."
            )
        return f


# ═══════════════════════════════════════════════════════════════════════
# 9. DASHBOARD COMPOSITE SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class StoreHealthScoreSerializer(serializers.Serializer):
    """Health score breakdown shown in the header popover."""
    overall_score = serializers.IntegerField()
    label = serializers.CharField()
    inventory_score = serializers.IntegerField()
    revenue_score = serializers.IntegerField()
    customer_score = serializers.IntegerField()
    operations_score = serializers.IntegerField()
    insights = serializers.ListField(child=serializers.CharField())


class KPICardSerializer(serializers.Serializer):
    """
    A single KPI tile on the dashboard.
    `sparkline` is a list of floats for the mini chart.
    `confidence_lower`/`upper` power the forecast card's band display.
    """
    key = serializers.CharField()
    label = serializers.CharField()
    value = serializers.DecimalField(max_digits=14, decimal_places=2)
    display_value = serializers.CharField()
    delta_percent = serializers.FloatField()
    trend_direction = serializers.ChoiceField(choices=['up', 'down', 'flat'])
    target = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True
    )
    progress_percent = serializers.FloatField(required=False, allow_null=True)
    sparkline = serializers.ListField(child=serializers.FloatField())
    confidence_lower = serializers.FloatField(required=False, allow_null=True)
    confidence_upper = serializers.FloatField(required=False, allow_null=True)


class LiveTickerItemSerializer(serializers.Serializer):
    """Single row on the scrolling ticker strip."""
    icon = serializers.CharField()
    label = serializers.CharField()
    value = serializers.CharField()
    severity = serializers.ChoiceField(
        choices=['info', 'warning', 'critical', 'success']
    )


class TopMoverSerializer(serializers.Serializer):
    """Entry in the 'Top Movers Today' widget."""
    rank = serializers.IntegerField()
    product_id = serializers.UUIDField()
    product_name = serializers.CharField()
    units_sold = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    delta_percent = serializers.FloatField()
    trend_direction = serializers.ChoiceField(choices=['up', 'down', 'flat'])


class DebtSummarySerializer(serializers.Serializer):
    """Aggregate for the debts widget on the right rail."""
    total_outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)
    debtor_count = serializers.IntegerField()
    overdue_count = serializers.IntegerField()
    oldest_overdue_days = serializers.IntegerField()
    top_debts = CustomerDebtSerializer(many=True)


class ActivityFeedItemSerializer(serializers.Serializer):
    """Live activity entry — unified feed across txns, AI, POs, customers."""
    kind = serializers.ChoiceField(
        choices=['sale', 'ai_alert', 'po_confirmed', 'customer', 'debt_reminder']
    )
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True)
    icon = serializers.CharField()
    severity = serializers.ChoiceField(
        choices=['info', 'warning', 'critical', 'success']
    )
    timestamp = serializers.DateTimeField()


class StockAlertSerializer(serializers.Serializer):
    """A product that is critical or low on stock."""
    product_id = serializers.UUIDField()
    product_name = serializers.CharField()
    sku = serializers.CharField()
    current_stock = serializers.IntegerField()
    reorder_point = serializers.IntegerField()
    days_remaining = serializers.FloatField(allow_null=True)
    tier = serializers.ChoiceField(choices=['critical', 'low'])


class DashboardSerializer(serializers.Serializer):
    """
    Composite payload for `GET /dashboard/` — one call drives the whole
    'Today's Pulse' tab. Avoids the 8-request waterfall the frontend
    would otherwise need.
    """
    store = StoreSerializer()
    health = StoreHealthScoreSerializer()
    kpis = KPICardSerializer(many=True)
    ticker = LiveTickerItemSerializer(many=True)
    insight_banner = AIRecommendationSerializer(allow_null=True)
    priority_actions = AIRecommendationSerializer(many=True)
    upcoming_forecast = DemandForecastSerializer(many=True)
    stock_alerts = StockAlertSerializer(many=True)
    top_movers = TopMoverSerializer(many=True)
    recent_activity = ActivityFeedItemSerializer(many=True)
    debt_summary = DebtSummarySerializer()
    generated_at = serializers.DateTimeField()


# ═══════════════════════════════════════════════════════════════════════
# 10. WHAT-IF SIMULATOR SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════

class WhatIfInputSerializer(serializers.Serializer):
    """
    Input for `POST /simulator/what-if/`. All inputs optional — omitted
    values default to the store's current state.
    """
    product_id = serializers.UUIDField(required=False, allow_null=True)
    price_change = serializers.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00')
    )
    quantity_change = serializers.IntegerField(default=0)
    ad_spend = serializers.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00')
    )
    staff_count = serializers.IntegerField(required=False, min_value=1)

    def validate_product_id(self, value):
        if value is None:
            return value
        store = self.context.get('store')
        if not Product.objects.filter(id=value, store=store).exists():
            raise serializers.ValidationError("Product not found in this store.")
        return value


class WhatIfOutputSerializer(serializers.Serializer):
    """Result of the What-If simulation."""
    projected_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    projected_profit = serializers.DecimalField(max_digits=14, decimal_places=2)
    delta_vs_baseline = serializers.DecimalField(max_digits=14, decimal_places=2)
    stockout_risk = serializers.ChoiceField(choices=['low', 'medium', 'high'])
    confidence = serializers.FloatField()
    assumptions = serializers.ListField(child=serializers.CharField())


class SalesTrendPointSerializer(serializers.Serializer):
    """One day of the sales trend series."""
    date = serializers.DateField()
    revenue = serializers.FloatField()
    transaction_count = serializers.IntegerField()
    is_event = serializers.BooleanField()
    deviation_percent = serializers.FloatField()


class SalesTrendResponseSerializer(serializers.Serializer):
    """Response shape for `GET /api/analytics/sales-trend/`."""
    days = serializers.IntegerField()
    series = SalesTrendPointSerializer(many=True)


# ═══════════════════════════════════════════════════════════════════════
# 11. SALES (RECEIPTS)
# ═══════════════════════════════════════════════════════════════════════

class SaleLineSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()
    sku = serializers.SerializerMethodField()

    class Meta:
        model = TransactionLog
        fields = [
            'id', 'product', 'product_name', 'sku',
            'quantity_sold', 'unit_price', 'unit_cost', 'total_amount',
        ]
        read_only_fields = fields

    def get_product_name(self, obj):
        return obj.product.name if obj.product_id else ''

    def get_sku(self, obj):
        return obj.product.sku if obj.product_id else ''


class SaleSerializer(serializers.ModelSerializer):
    """Read-only receipt view, including its lines."""
    lines = SaleLineSerializer(many=True, read_only=True)
    customer_name = serializers.SerializerMethodField()
    customer_phone = serializers.SerializerMethodField()
    cashier_name = serializers.SerializerMethodField()
    balance_due = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()
    payment_method_label = serializers.CharField(source='get_payment_method_display', read_only=True)

    class Meta:
        model = Sale
        fields = [
            'id', 'receipt_number', 'status', 'payment_method', 'payment_method_label',
            'customer', 'customer_name', 'customer_phone', 'cashier', 'cashier_name',
            'subtotal', 'tax_amount', 'total_amount', 'amount_paid', 'balance_due',
            'notes', 'sold_at', 'voided_at', 'void_reason', 'item_count', 'lines',
        ]
        read_only_fields = fields

    def get_customer_name(self, obj):
        return obj.customer.name if obj.customer_id else None

    def get_customer_phone(self, obj):
        return obj.customer.phone_number if obj.customer_id else None

    def get_cashier_name(self, obj):
        if not obj.cashier_id:
            return None
        return obj.cashier.full_name or obj.cashier.username

    def get_balance_due(self, obj):
        total = sum((d.outstanding_amount for d in obj.debts.all()), Decimal('0.00'))
        return str(total)

    def get_item_count(self, obj):
        return sum(line.quantity_sold for line in obj.lines.all())


class SaleItemInputSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=100000)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.00'),
        required=False, allow_null=True,
    )


class SaleCreateSerializer(serializers.Serializer):
    """Payload for `POST /api/sales/` — a whole cart in one request."""
    items = SaleItemInputSerializer(many=True, allow_empty=False)
    customer = serializers.UUIDField(required=False, allow_null=True)
    payment_method = serializers.ChoiceField(
        choices=Sale.PaymentMethod.choices, default=Sale.PaymentMethod.CASH
    )
    amount_paid = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.00'),
        required=False, allow_null=True,
    )
    due_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_items(self, items):
        if len(items) > 200:
            raise serializers.ValidationError("A single sale can have at most 200 lines.")
        return items

    def validate_customer(self, value):
        if value is None:
            return None
        store = self.context.get('store')
        customer = Customer.objects.filter(id=value, store=store, is_active=True).first()
        if customer is None:
            raise serializers.ValidationError("Customer not found in this store.")
        return customer


class VoidSaleSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


# ═══════════════════════════════════════════════════════════════════════
# 12. STOCK LEDGER
# ═══════════════════════════════════════════════════════════════════════

class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source='product.name')
    sku = serializers.ReadOnlyField(source='product.sku')
    movement_label = serializers.CharField(source='get_movement_type_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            'id', 'product', 'product_name', 'sku', 'movement_type', 'movement_label',
            'quantity_change', 'stock_before', 'stock_after',
            'reference_type', 'reference_id', 'note',
            'created_by', 'created_by_name', 'created_at',
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        if not obj.created_by_id:
            return None
        return obj.created_by.full_name or obj.created_by.username


class AdjustStockSerializer(serializers.Serializer):
    """Payload for `POST /api/products/{id}/adjust-stock/`."""
    mode = serializers.ChoiceField(choices=['delta', 'set'], default='delta')
    quantity = serializers.IntegerField()
    reason = serializers.ChoiceField(
        choices=[c for c in StockMovement.Type.choices
                 if c[0] in ('ADJUSTMENT', 'DAMAGE', 'EXPIRED', 'COUNT')],
        default='ADJUSTMENT',
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


# ═══════════════════════════════════════════════════════════════════════
# 13. PURCHASE ORDER ACTIONS
# ═══════════════════════════════════════════════════════════════════════

class ConfirmPurchaseSerializer(serializers.Serializer):
    expected_delivery = serializers.DateField(required=False, allow_null=True)


class ReceiveItemSerializer(serializers.Serializer):
    item_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=0)


class ReceivePurchaseSerializer(serializers.Serializer):
    """Payload for `POST /api/purchase-orders/{id}/receive/` — units received now."""
    items = ReceiveItemSerializer(many=True, allow_empty=False)
    update_cost_price = serializers.BooleanField(default=False)


# ═══════════════════════════════════════════════════════════════════════
# 14. DEBT REPAYMENTS & EXPENSES
# ═══════════════════════════════════════════════════════════════════════

class DebtPaymentSerializer(serializers.ModelSerializer):
    received_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DebtPayment
        fields = ['id', 'debt', 'amount', 'method', 'note', 'received_by_name', 'paid_at']
        read_only_fields = fields

    def get_received_by_name(self, obj):
        if not obj.received_by_id:
            return None
        return obj.received_by.full_name or obj.received_by.username


class RecordDebtPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    method = serializers.ChoiceField(choices=DebtPayment.Method.choices, default=DebtPayment.Method.CASH)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ExpenseSerializer(TenantScopedSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [
            'id', 'store', 'category', 'category_label', 'description', 'amount',
            'expense_date', 'payment_method', 'recorded_by', 'recorded_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'store', 'recorded_by', 'created_at', 'updated_at',
        ]
        extra_kwargs = {'expense_date': {'required': False}}

    def get_recorded_by_name(self, obj):
        if not obj.recorded_by_id:
            return None
        return obj.recorded_by.full_name or obj.recorded_by.username

    def create(self, validated_data):
        store = self._get_store()
        validated_data.setdefault('expense_date', local_now(store).date())
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if getattr(user, 'is_authenticated', False):
            validated_data['recorded_by'] = user
        return super().create(validated_data)
