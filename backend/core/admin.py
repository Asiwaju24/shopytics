from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    User, Store, StoreMembership, Supplier, Product, ProductSupplier,
    Customer, CustomerDebt, TransactionLog, DailySalesSummary,
    PurchaseOrder, PurchaseOrderItem, StructuralBreakLog,
    DemandForecast, AIRecommendation, AIActionLog,
    Sale, StockMovement, DebtPayment, Expense,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('username', 'email', 'full_name', 'phone_number', 'is_active', 'is_verified')
    search_fields = ('username', 'email', 'full_name', 'phone_number')
    ordering = ('username',)
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('iPace profile', {'fields': ('phone_number', 'full_name', 'profile_picture_url', 'is_verified')}),
    )


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'business_type', 'currency', 'city', 'country', 'is_active')
    list_filter = ('business_type', 'currency', 'is_active')
    search_fields = ('name', 'owner__username', 'owner__email', 'whatsapp_number')


@admin.register(StoreMembership)
class StoreMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'store', 'role', 'is_active', 'joined_at')
    list_filter = ('role', 'is_active')
    search_fields = ('user__username', 'store__name')


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'store', 'contact_person', 'phone_number', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'store__name', 'phone_number')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'store', 'current_stock', 'reorder_point', 'selling_price', 'is_active')
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'sku', 'store__name')


@admin.register(ProductSupplier)
class ProductSupplierAdmin(admin.ModelAdmin):
    list_display = ('product', 'supplier', 'is_preferred', 'last_cost_price')
    list_filter = ('is_preferred',)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'store', 'phone_number', 'total_lifetime_value', 'is_active')
    search_fields = ('name', 'phone_number', 'store__name')


@admin.register(CustomerDebt)
class CustomerDebtAdmin(admin.ModelAdmin):
    list_display = ('customer', 'store', 'amount', 'amount_paid', 'due_date', 'is_settled')
    list_filter = ('is_settled',)
    search_fields = ('customer__name', 'store__name')


@admin.register(TransactionLog)
class TransactionLogAdmin(admin.ModelAdmin):
    list_display = ('store', 'product', 'quantity_sold', 'total_amount', 'source', 'transaction_date')
    list_filter = ('source',)
    search_fields = ('store__name', 'product__name', 'external_id')
    date_hierarchy = 'transaction_date'


@admin.register(DailySalesSummary)
class DailySalesSummaryAdmin(admin.ModelAdmin):
    list_display = ('store', 'product', 'date', 'total_quantity_sold', 'total_revenue')
    list_filter = ('seasonality_flag', 'is_weekend')
    date_hierarchy = 'date'


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('store', 'supplier', 'status', 'total_cost', 'created_at')
    list_filter = ('status',)
    search_fields = ('store__name', 'supplier__name')
    inlines = [PurchaseOrderItemInline]


@admin.register(StructuralBreakLog)
class StructuralBreakLogAdmin(admin.ModelAdmin):
    list_display = ('store', 'product', 'break_date', 'method', 'change_percent')
    list_filter = ('method',)


@admin.register(DemandForecast)
class DemandForecastAdmin(admin.ModelAdmin):
    list_display = ('store', 'product', 'horizon', 'forecast_date', 'predicted_quantity', 'confidence_score')
    list_filter = ('horizon', 'model_version')


@admin.register(AIRecommendation)
class AIRecommendationAdmin(admin.ModelAdmin):
    list_display = ('store', 'title', 'category', 'priority', 'is_resolved', 'created_at')
    list_filter = ('priority', 'category', 'is_resolved')
    search_fields = ('title', 'store__name')


@admin.register(AIActionLog)
class AIActionLogAdmin(admin.ModelAdmin):
    list_display = ('store', 'recommendation', 'user', 'action_taken', 'created_at')
    list_filter = ('action_taken',)


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('receipt_number', 'store', 'customer', 'payment_method', 'total_amount', 'status', 'sold_at')
    list_filter = ('status', 'payment_method')
    search_fields = ('receipt_number', 'store__name', 'customer__name')
    date_hierarchy = 'sold_at'
    readonly_fields = ('receipt_seq', 'receipt_number')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'store', 'movement_type', 'quantity_change', 'stock_after', 'created_by', 'created_at')
    list_filter = ('movement_type',)
    search_fields = ('product__name', 'product__sku', 'note')
    date_hierarchy = 'created_at'


@admin.register(DebtPayment)
class DebtPaymentAdmin(admin.ModelAdmin):
    list_display = ('debt', 'store', 'amount', 'method', 'received_by', 'paid_at')
    list_filter = ('method',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('description', 'store', 'category', 'amount', 'expense_date', 'recorded_by')
    list_filter = ('category', 'payment_method')
    search_fields = ('description', 'store__name')
    date_hierarchy = 'expense_date'
