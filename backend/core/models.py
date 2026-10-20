"""
iPace SME Business Co-Pilot — Core Data Models
================================================
Multi-tenant SaaS backend powering proactive business intelligence for
African SMEs: forecasting, stockout prevention, WhatsApp-native supplier
ordering, customer debt tracking, and AI-driven recommendations.

Fixed & Hardened Version:
- Clean/Validation execution enforced on save()
- Decimal quantization guards (ROUND_HALF_UP) against float precision bugs
- Unique constraints updated to handle NULL product deletions cleanly
- Hard-delete blocked on Product to prevent CASCADE data wipes
- Optimised indexes for timeline and multi-horizon forecast queries
"""

import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


def quantize_money(amount: Decimal) -> Decimal:
    """Helper to enforce strict 2-decimal place precision for currency fields."""
    if amount is None:
        return Decimal('0.00')
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


# ═══════════════════════════════════════════════════════════════════════
# 1. USER, TENANT & ORGANIZATIONAL MODELS
# ═══════════════════════════════════════════════════════════════════════

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    profile_picture_url = models.URLField(blank=True)
    is_verified = models.BooleanField(default=False)
    last_active_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['phone_number']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return self.full_name or self.username or str(self.id)


class Store(models.Model):
    class Currency(models.TextChoices):
        NGN = 'NGN', 'Nigerian Naira (₦)'
        USD = 'USD', 'US Dollar ($)'
        GHS = 'GHS', 'Ghanaian Cedi (₵)'
        KES = 'KES', 'Kenyan Shilling (KSh)'
        ZAR = 'ZAR', 'South African Rand (R)'

    class BusinessType(models.TextChoices):
        GROCERY = 'GROCERY', 'Retail Grocery'
        PHARMACY = 'PHARMACY', 'Pharmacy'
        BOUTIQUE = 'BOUTIQUE', 'Boutique / Fashion'
        SUPERMARKET = 'SUPERMARKET', 'Supermarket'
        RESTAURANT = 'RESTAURANT', 'Restaurant / QSR'
        ELECTRONICS = 'ELECTRONICS', 'Electronics'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='owned_stores'
    )
    name = models.CharField(max_length=255)
    business_type = models.CharField(
        max_length=20, choices=BusinessType.choices, default=BusinessType.GROCERY
    )
    currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.NGN
    )
    timezone = models.CharField(max_length=64, default='Africa/Lagos')
    whatsapp_number = models.CharField(
        max_length=20, help_text="Primary phone for automated alerts"
    )
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default='Nigeria')

    daily_revenue_target = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0.00')
    )
    tax_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="VAT / sales tax added on top of every sale (0 for none)."
    )
    receipt_footer = models.CharField(
        max_length=255, blank=True, default='Thank you for shopping with us!'
    )
    enable_auto_pilot = models.BooleanField(
        default=False,
        help_text="If True, AI can auto-send POs when critical thresholds are hit"
    )

    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['owner', 'is_active']),
            models.Index(fields=['is_active', 'business_type']),
        ]

    def __str__(self):
        return self.name

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_active', 'deleted_at', 'updated_at'])


class StoreMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Owner'
        MANAGER = 'MANAGER', 'Manager'
        CASHIER = 'CASHIER', 'Cashier'
        ACCOUNTANT = 'ACCOUNTANT', 'Accountant'
        VIEWER = 'VIEWER', 'Viewer'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='memberships'
    )
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='members'
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'store')
        indexes = [
            models.Index(fields=['store', 'role', 'is_active']),
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        return f"{self.user} @ {self.store} ({self.role})"


# ═══════════════════════════════════════════════════════════════════════
# 2. SUPPLIER NETWORK
# ═══════════════════════════════════════════════════════════════════════

class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='suppliers'
    )
    name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255, blank=True)
    whatsapp_number = models.CharField(max_length=20)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    lead_time_days = models.PositiveIntegerField(default=2)
    reliability_score = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    quality_score = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    notes = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['store', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.store.name})"

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_active', 'deleted_at', 'updated_at'])


# ═══════════════════════════════════════════════════════════════════════
# 3. INVENTORY & PRODUCT CATALOG
# ═══════════════════════════════════════════════════════════════════════

class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='products'
    )
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100, blank=True, default='General')
    description = models.TextField(blank=True)

    cost_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(0)]
    )
    selling_price = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)]
    )
    current_stock = models.IntegerField(
        default=0, validators=[MinValueValidator(0)]
    )
    reorder_point = models.IntegerField(
        default=10, validators=[MinValueValidator(0)]
    )
    safety_stock = models.IntegerField(
        default=0, validators=[MinValueValidator(0)]
    )

    unit_of_measure = models.CharField(max_length=20, default='unit')
    image_url = models.URLField(blank=True)

    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('store', 'sku')
        ordering = ['name']
        indexes = [
            models.Index(fields=['store', 'is_active']),
            models.Index(fields=['store', 'current_stock']),
            models.Index(fields=['store', 'category', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.sku}) — {self.store.name}"

    @property
    def margin_percent(self) -> float:
        if self.selling_price <= 0:
            return 0.0
        return float(
            (self.selling_price - self.cost_price) / self.selling_price * 100
        )

    def delete(self, using=None, keep_parents=False):
        """FIX #3: Overridden to prevent hard deletes from wiping historical forecast & PO tables."""
        self.soft_delete()

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_active', 'deleted_at', 'updated_at'])


class ProductSupplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='supplier_links'
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name='product_links'
    )
    supplier_sku = models.CharField(max_length=100, blank=True)
    last_cost_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    minimum_order_qty = models.PositiveIntegerField(default=1)
    is_preferred = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('product', 'supplier')
        indexes = [
            models.Index(fields=['product', 'is_preferred']),
        ]


# ═══════════════════════════════════════════════════════════════════════
# 4. CUSTOMERS & DEBT TRACKING
# ═══════════════════════════════════════════════════════════════════════

class Customer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='customers'
    )
    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, db_index=True)
    whatsapp_opt_in = models.BooleanField(default=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    total_lifetime_value = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0.00')
    )
    total_orders = models.PositiveIntegerField(default=0)
    last_purchase_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('store', 'phone_number')
        ordering = ['-last_purchase_at']
        indexes = [
            models.Index(fields=['store', 'is_active']),
            models.Index(fields=['store', 'last_purchase_at']),
        ]

    def __str__(self):
        return f"{self.name} ({self.phone_number})"


class CustomerDebt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='customer_debts'
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name='debts'
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(0)]
    )
    due_date = models.DateField()
    notes = models.TextField(blank=True)
    sale = models.ForeignKey(
        'Sale', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='debts'
    )

    is_settled = models.BooleanField(default=False)
    settled_at = models.DateTimeField(null=True, blank=True)
    last_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_date']
        indexes = [
            models.Index(fields=['store', 'is_settled', 'due_date']),
            models.Index(fields=['customer', 'is_settled']),
        ]

    def __str__(self):
        return f"{self.customer.name} — ₦{self.outstanding_amount}"

    @property
    def outstanding_amount(self) -> Decimal:
        return quantize_money(max(self.amount - self.amount_paid, Decimal('0.00')))

    @property
    def days_overdue(self) -> int:
        if self.is_settled:
            return 0
        delta = timezone.now().date() - self.due_date
        return max(delta.days, 0)


# ═══════════════════════════════════════════════════════════════════════
# 5. TRANSACTIONS & SALES DATA
# ═══════════════════════════════════════════════════════════════════════

class TransactionLog(models.Model):
    class Source(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual Entry'
        CSV_IMPORT = 'CSV_IMPORT', 'CSV / Excel Import'
        POS_SYNC = 'POS_SYNC', 'POS Integration'
        WHATSAPP = 'WHATSAPP', 'WhatsApp Order'
        API = 'API', 'API'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='transactions'
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions'
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions'
    )

    sale = models.ForeignKey(
        'Sale', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='lines'
    )
    is_voided = models.BooleanField(default=False, db_index=True)

    quantity_sold = models.IntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)]
    )
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)

    currency = models.CharField(max_length=3, default='NGN')
    transaction_date = models.DateTimeField(db_index=True)
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.MANUAL
    )
    external_id = models.CharField(
        max_length=128, blank=True, db_index=True
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-transaction_date']
        indexes = [
            models.Index(fields=['store', 'transaction_date']),
            models.Index(fields=['product', 'transaction_date']),
            models.Index(fields=['store', 'source']),
            models.Index(fields=['store', 'is_voided', 'transaction_date'], name='txn_store_live_date_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['store', 'external_id'],
                condition=~Q(external_id=''),
                name='unique_external_txn_per_store',
            ),
        ]

    def __str__(self):
        return f"{self.product} × {self.quantity_sold} @ {self.transaction_date:%Y-%m-%d}"

    def clean(self):
        """FIX #1: Clean validation logic executed reliably before saving."""
        super().clean()
        if self.quantity_sold and self.unit_price:
            expected = quantize_money(Decimal(self.quantity_sold) * self.unit_price)
            if self.total_amount and abs(self.total_amount - expected) > Decimal('0.01'):
                raise ValidationError({
                    'total_amount': f"Expected {expected}, got {self.total_amount}"
                })

    def save(self, *args, **kwargs):
        """FIX #1 & #4: Auto-computes quantised totals and enforces full_clean() on save."""
        self.total_amount = quantize_money(Decimal(self.quantity_sold) * self.unit_price)
        
        if not self.unit_cost and self.product_id:
            self.unit_cost = self.product.cost_price
        if not self.currency and self.store_id:
            self.currency = self.store.currency

        self.full_clean()  # Guarantees clean() validation runs
        super().save(*args, **kwargs)


class DailySalesSummary(models.Model):
    class SeasonalityFlag(models.TextChoices):
        NORMAL = 'NORMAL', 'Normal Day'
        WEEKEND = 'WEEKEND', 'Weekend'
        HOLIDAY = 'HOLIDAY', 'Holiday'
        PAYDAY_WEEK = 'PAYDAY_WEEK', 'Payday Week'
        PROMO = 'PROMO', 'Promotion Day'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='daily_summaries'
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='daily_summaries'
    )
    product_name_snapshot = models.CharField(max_length=255, blank=True)

    date = models.DateField(db_index=True)
    total_quantity_sold = models.IntegerField(default=0)
    total_revenue = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0.00')
    )
    total_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0.00')
    )
    transaction_count = models.PositiveIntegerField(default=0)
    average_unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00')
    )

    seasonality_flag = models.CharField(
        max_length=20, choices=SeasonalityFlag.choices,
        default=SeasonalityFlag.NORMAL
    )
    is_weekend = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # FIX #2: Include store to prevent unique constraint crashes when product is NULL
        unique_together = ('store', 'product', 'date')
        ordering = ['-date']
        indexes = [
            models.Index(fields=['store', 'date']),
            models.Index(fields=['product', 'date']),
            models.Index(fields=['store', 'date', 'seasonality_flag']),
        ]

    def __str__(self):
        return f"{self.product_name_snapshot or self.product_id} — {self.date}"

    @property
    def gross_profit(self) -> Decimal:
        return quantize_money(self.total_revenue - self.total_cost)


# ═══════════════════════════════════════════════════════════════════════
# 6. PURCHASE ORDERS
# ═══════════════════════════════════════════════════════════════════════

class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SENT = 'SENT', 'Sent via WhatsApp'
        CONFIRMED = 'CONFIRMED', 'Supplier Confirmed'
        PARTIAL = 'PARTIAL', 'Partially Delivered'
        DELIVERED = 'DELIVERED', 'Delivered'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='purchase_orders'
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name='purchase_orders'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_purchase_orders'
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    whatsapp_message = models.TextField(blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    expected_delivery = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    total_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0.00')
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['store', 'status']),
            models.Index(fields=['supplier', 'status']),
            models.Index(fields=['store', 'expected_delivery']),
        ]


class PurchaseOrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='items'
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='po_items'
    )
    quantity_ordered = models.PositiveIntegerField()
    quantity_received = models.PositiveIntegerField(default=0)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)]
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['purchase_order']),
            models.Index(fields=['product']),
        ]

    @property
    def line_total(self) -> Decimal:
        return quantize_money(Decimal(self.quantity_ordered) * self.unit_cost)


# ═══════════════════════════════════════════════════════════════════════
# 7. ANALYTICS, FORECASTING & AI ENGINE
# ═══════════════════════════════════════════════════════════════════════

class StructuralBreakLog(models.Model):
    class Method(models.TextChoices):
        CHOW = 'CHOW', 'Chow Test'
        CUSUM = 'CUSUM', 'CUSUM'
        BAYESIAN = 'BAYESIAN', 'Bayesian Change Point'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='structural_breaks'
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='structural_breaks'
    )

    break_date = models.DateField()
    method = models.CharField(
        max_length=20, choices=Method.choices, default=Method.CHOW
    )
    p_value = models.FloatField()
    test_statistic = models.FloatField(null=True, blank=True)
    pre_break_avg_daily_sales = models.FloatField()
    post_break_avg_daily_sales = models.FloatField()
    change_percent = models.FloatField(default=0.0)
    notes = models.TextField(blank=True)

    detected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-detected_at']
        indexes = [
            models.Index(fields=['store', 'product', 'break_date']),
        ]


class DemandForecast(models.Model):
    class Horizon(models.TextChoices):
        D1 = 'D1', 'Next Day'
        D3 = 'D3', 'Next 3 Days'
        D7 = 'D7', 'Next 7 Days'
        D14 = 'D14', 'Next 14 Days'
        D30 = 'D30', 'Next 30 Days'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='forecasts'
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='forecasts'
    )

    horizon = models.CharField(
        max_length=5, choices=Horizon.choices, default=Horizon.D7
    )
    forecast_date = models.DateField()
    predicted_quantity = models.FloatField()
    predicted_revenue = models.DecimalField(max_digits=14, decimal_places=2)
    confidence_lower = models.FloatField()
    confidence_upper = models.FloatField()
    confidence_score = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    model_version = models.CharField(max_length=50, default='prophet_v1')
    model_metadata = models.JSONField(default=dict, blank=True)

    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('product', 'forecast_date', 'horizon', 'model_version')
        ordering = ['-generated_at']
        # FIX #5: Updated indexes to support rapid timeline queries sorted by generated_at
        indexes = [
            models.Index(fields=['store', '-generated_at']),
            models.Index(fields=['product', 'horizon', '-generated_at']),
        ]


class AIRecommendation(models.Model):
    class Priority(models.TextChoices):
        CRITICAL = 'CRITICAL', '🔴 Critical (Action Required Now)'
        WARNING = 'WARNING', '🟡 Warning (Attention Needed)'
        INFO = 'INFO', '🟢 Info (Good News / Opportunity)'

    class Category(models.TextChoices):
        STOCKOUT_RISK = 'STOCKOUT_RISK', 'Stockout Risk'
        REGIME_SHIFT = 'REGIME_SHIFT', 'Regime Shift / Trend Change'
        REORDER_SUGGESTION = 'REORDER_SUGGESTION', 'Reorder Suggestion'
        PROFIT_OPPORTUNITY = 'PROFIT_OPPORTUNITY', 'Profit Opportunity'
        DEBT_REMINDER = 'DEBT_REMINDER', 'Customer Debt Reminder'
        PRICE_ADJUSTMENT = 'PRICE_ADJUSTMENT', 'Price Adjustment'
        CUSTOMER_INSIGHT = 'CUSTOMER_INSIGHT', 'Customer Insight'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='recommendations'
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recommendations'
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recommendations'
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recommendations'
    )
    forecast = models.ForeignKey(
        DemandForecast, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recommendations'
    )

    priority = models.CharField(
        max_length=20, choices=Priority.choices, default=Priority.INFO
    )
    category = models.CharField(max_length=30, choices=Category.choices)

    title = models.CharField(max_length=255)
    body = models.TextField()
    action_text = models.CharField(max_length=100, blank=True)
    suggested_reorder_qty = models.IntegerField(null=True, blank=True)

    estimated_financial_impact = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    confidence_score = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['store', 'is_resolved', 'priority']),
            models.Index(fields=['store', 'category', 'is_resolved']),
            models.Index(fields=['store', 'created_at']),
        ]

    def resolve(self, resolved_by_user=None):
        """FIX #6: Idempotent resolution method preventing double logs."""
        if self.is_resolved:
            return

        self.is_resolved = True
        self.resolved_at = timezone.now()
        self.save(update_fields=['is_resolved', 'resolved_at', 'updated_at'])

        if resolved_by_user:
            AIActionLog.objects.create(
                store=self.store,
                recommendation=self,
                action_taken=AIActionLog.Action.ACCEPTED,
                user=resolved_by_user,
            )


class AIActionLog(models.Model):
    class Action(models.TextChoices):
        VIEWED = 'VIEWED', 'Viewed'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        DISMISSED = 'DISMISSED', 'Dismissed'
        MODIFIED = 'MODIFIED', 'Modified Before Acting'
        AUTO_EXECUTED = 'AUTO_EXECUTED', 'Auto-Executed by Co-Pilot'
        SNOOZED = 'SNOOZED', 'Snoozed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='ai_action_logs'
    )
    recommendation = models.ForeignKey(
        AIRecommendation, on_delete=models.CASCADE, related_name='action_logs'
    )
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ai_actions'
    )
    action_taken = models.CharField(max_length=20, choices=Action.choices)
    feedback = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['store', 'action_taken']),
            models.Index(fields=['recommendation', 'action_taken']),
            models.Index(fields=['store', 'created_at']),
        ]


# ═══════════════════════════════════════════════════════════════════════
# 8. SALES (RECEIPTS), STOCK LEDGER, DEBT REPAYMENTS & EXPENSES
# ═══════════════════════════════════════════════════════════════════════

class Sale(models.Model):
    """
    One checkout / receipt. Each product on the receipt is a `TransactionLog`
    row (`lines`), so all existing analytics keep working off TransactionLog.
    Revenue is the sum of line totals (excludes tax); `total_amount` is what
    the customer actually pays (lines + tax).
    """
    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        TRANSFER = 'TRANSFER', 'Bank Transfer'
        CARD = 'CARD', 'Card / POS'
        CREDIT = 'CREDIT', 'On Credit'

    class Status(models.TextChoices):
        COMPLETED = 'COMPLETED', 'Completed'
        VOIDED = 'VOIDED', 'Voided'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='sales'
    )
    receipt_seq = models.PositiveIntegerField()
    receipt_number = models.CharField(max_length=32)
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sales'
    )
    cashier = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sales_made'
    )

    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.COMPLETED
    )

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True)

    sold_at = models.DateTimeField(default=timezone.now, db_index=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sales_voided'
    )
    void_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sold_at', '-receipt_seq']
        unique_together = ('store', 'receipt_seq')
        indexes = [
            models.Index(fields=['store', 'sold_at'], name='sale_store_sold_idx'),
            models.Index(fields=['store', 'status'], name='sale_store_status_idx'),
        ]

    def __str__(self):
        return f"{self.receipt_number} — {self.store.name}"


class StockMovement(models.Model):
    """
    Append-only stock ledger. Every change to `Product.current_stock` made by
    the application is recorded here with before/after levels, so any stock
    number can be traced back to the sales, deliveries and adjustments that
    produced it.
    """
    class Type(models.TextChoices):
        OPENING = 'OPENING', 'Opening Stock'
        SALE = 'SALE', 'Sale'
        SALE_VOID = 'SALE_VOID', 'Sale Voided (Restocked)'
        PURCHASE = 'PURCHASE', 'Purchase Received'
        ADJUSTMENT = 'ADJUSTMENT', 'Manual Adjustment'
        DAMAGE = 'DAMAGE', 'Damaged'
        EXPIRED = 'EXPIRED', 'Expired'
        COUNT = 'COUNT', 'Stock Count Correction'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='stock_movements'
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='movements'
    )
    movement_type = models.CharField(max_length=20, choices=Type.choices)
    quantity_change = models.IntegerField()
    stock_before = models.IntegerField()
    stock_after = models.IntegerField()
    reference_type = models.CharField(max_length=30, blank=True)
    reference_id = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_movements'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['store', 'created_at'], name='stockmv_store_created_idx'),
            models.Index(fields=['product', 'created_at'], name='stockmv_product_created_idx'),
        ]

    def __str__(self):
        return f"{self.product_id} {self.quantity_change:+d} ({self.movement_type})"


class DebtPayment(models.Model):
    """A repayment received against a `CustomerDebt`."""
    class Method(models.TextChoices):
        CASH = 'CASH', 'Cash'
        TRANSFER = 'TRANSFER', 'Bank Transfer'
        CARD = 'CARD', 'Card / POS'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='debt_payments'
    )
    debt = models.ForeignKey(
        CustomerDebt, on_delete=models.CASCADE, related_name='payments'
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    method = models.CharField(
        max_length=20, choices=Method.choices, default=Method.CASH
    )
    note = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='debt_payments_received'
    )
    paid_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_at']
        indexes = [
            models.Index(fields=['store', 'paid_at'], name='debtpay_store_paid_idx'),
        ]


class Expense(models.Model):
    """Money the business spends that isn't stock (rent, salaries, power...)."""
    class Category(models.TextChoices):
        RENT = 'RENT', 'Rent'
        SALARIES = 'SALARIES', 'Salaries & Wages'
        UTILITIES = 'UTILITIES', 'Utilities (power, water, data)'
        TRANSPORT = 'TRANSPORT', 'Transport & Delivery'
        MARKETING = 'MARKETING', 'Marketing'
        SUPPLIES = 'SUPPLIES', 'Supplies & Packaging'
        MAINTENANCE = 'MAINTENANCE', 'Repairs & Maintenance'
        TAXES = 'TAXES', 'Taxes & Licences'
        OTHER = 'OTHER', 'Other'

    class Method(models.TextChoices):
        CASH = 'CASH', 'Cash'
        TRANSFER = 'TRANSFER', 'Bank Transfer'
        CARD = 'CARD', 'Card / POS'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        Store, on_delete=models.CASCADE, related_name='expenses'
    )
    category = models.CharField(
        max_length=30, choices=Category.choices, default=Category.OTHER
    )
    description = models.CharField(max_length=255)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    expense_date = models.DateField(db_index=True)
    payment_method = models.CharField(
        max_length=20, choices=Method.choices, default=Method.CASH
    )
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='expenses_recorded'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-expense_date', '-created_at']
        indexes = [
            models.Index(fields=['store', 'expense_date'], name='expense_store_date_idx'),
        ]

    def __str__(self):
        return f"{self.description} — {self.amount}"
