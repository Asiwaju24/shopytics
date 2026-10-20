"""
iPace SME Business Co-Pilot — Reports
GET /api/reports/summary/                    profit & loss + breakdowns
GET /api/reports/export/<kind>/?fmt=csv|xlsx downloads (sales, sale-items, inventory, debtors, expenses)
Restricted to owner, manager and accountant — they contain profit figures.
(The parameter is `fmt`, not `format`: DRF reserves `?format=` for content negotiation.)
"""
from rest_framework import exceptions, permissions, views
from rest_framework.response import Response

from .base import IsStoreMember, HasReadRole
from ..models import StoreMembership
from ..services.formatting import resolve_period
from ..services.reports import EXPORTERS, build_summary, render_export

Role = StoreMembership.Role
REPORT_ROLES = {Role.OWNER, Role.MANAGER, Role.ACCOUNTANT}


class _ReportBase(views.APIView):
    permission_classes = [permissions.IsAuthenticated, IsStoreMember, HasReadRole]
    read_roles = REPORT_ROLES

    def period(self, request):
        return resolve_period(
            request.store,
            request.query_params.get('date_from'),
            request.query_params.get('date_to'),
        )


class ReportSummaryView(_ReportBase):
    def get(self, request):
        store = request.store
        date_from, date_to, start, end = self.period(request)
        return Response(build_summary(store, date_from, date_to, start, end))


class ReportExportView(_ReportBase):
    def get(self, request, kind):
        if kind not in EXPORTERS:
            raise exceptions.NotFound("Unknown report.")
        fmt = request.query_params.get('fmt', 'csv').lower()
        if fmt not in ('csv', 'xlsx'):
            raise exceptions.ValidationError({'detail': "fmt must be 'csv' or 'xlsx'."})
        date_from, date_to, start, end = self.period(request)
        return render_export(request.store, kind, fmt, date_from, date_to, start, end)
