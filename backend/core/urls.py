"""
iPace SME Business Co-Pilot — URL Configuration
================================================
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.resources import (
    SupplierViewSet, ProductViewSet, ProductSupplierViewSet,
    CustomerViewSet, CustomerDebtViewSet, TransactionLogViewSet,
    PurchaseOrderViewSet, AIRecommendationViewSet,
    StockMovementViewSet, ExpenseViewSet,
)
from .views.sales import SaleViewSet
from .views.reports import ReportSummaryView, ReportExportView
from .views.store import StoreViewSet, StoreMembershipViewSet
from .views.analytics import DashboardView, WhatIfSimulatorView, SalesTrendView
from .views.auth import RegisterView, LoginView, RefreshView, LogoutView, MeView, ChangePasswordView

router = DefaultRouter()
router.register(r'stores', StoreViewSet, basename='store')
router.register(r'memberships', StoreMembershipViewSet, basename='membership')
router.register(r'suppliers', SupplierViewSet, basename='supplier')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'product-suppliers', ProductSupplierViewSet, basename='product-supplier')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'debts', CustomerDebtViewSet, basename='debt')
router.register(r'transactions', TransactionLogViewSet, basename='transaction')
router.register(r'purchase-orders', PurchaseOrderViewSet, basename='purchase-order')
router.register(r'recommendations', AIRecommendationViewSet, basename='recommendation')
router.register(r'sales', SaleViewSet, basename='sale')
router.register(r'stock-movements', StockMovementViewSet, basename='stock-movement')
router.register(r'expenses', ExpenseViewSet, basename='expense')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', DashboardView.as_view(), name='dashboard-pulse'),
    path('simulator/what-if/', WhatIfSimulatorView.as_view(), name='simulator-what-if'),
    path('analytics/sales-trend/', SalesTrendView.as_view(), name='sales-trend'),
    path('reports/summary/', ReportSummaryView.as_view(), name='report-summary'),
    path('reports/export/<str:kind>/', ReportExportView.as_view(), name='report-export'),

    # Auth — excluded from store-context resolution (see StoreContextMiddleware.EXCLUDED_PREFIXES)
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/login/', LoginView.as_view(), name='auth-login'),
    path('auth/refresh/', RefreshView.as_view(), name='auth-refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/me/', MeView.as_view(), name='auth-me'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='auth-change-password'),
]
