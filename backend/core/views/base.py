"""
iPace SME Business Co-Pilot — Base Views & Scoping
===================================================
"""

from rest_framework import viewsets, permissions, exceptions

from ..middleware import resolve_store_for_user
from ..models import StoreMembership


class IsStoreMember(permissions.BasePermission):
    """Guarantees the requesting user has an active membership in the request's store context."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        store = getattr(request, 'store', None)
        if not store:
            # StoreContextMiddleware runs before DRF authentication, so for
            # requests authenticated at the DRF layer only (tokens, JWT,
            # force_authenticate in tests, etc.) request.user wasn't known
            # yet when the middleware ran and request.store is still None.
            # Re-resolve it now that request.user is authoritative.
            raw_store_id = request.headers.get('X-Store-ID') or request.GET.get('store_id')
            store = resolve_store_for_user(request.user, raw_store_id)
            request.store = store

        if not store:
            return False
        # StoreMembership.store has related_name='members' (see core/models.py)
        return store.members.filter(user=request.user, is_active=True).exists()


class HasWriteRole(permissions.BasePermission):
    """
    Read access (list/retrieve) is open to any active store member —
    `IsStoreMember` already guarantees that. Write access (create/update/
    delete) additionally requires the member's role to be one of the
    view's `write_roles` (defaults to OWNER + MANAGER). Set `write_roles`
    on a ViewSet to widen or narrow this per resource, e.g. cashiers
    recording sales but not editing the product catalog.
    """
    default_write_roles = {StoreMembership.Role.OWNER, StoreMembership.Role.MANAGER}

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        store = getattr(request, 'store', None)
        if not store or not request.user or not request.user.is_authenticated:
            return False

        write_roles = getattr(view, 'write_roles', self.default_write_roles)
        return store.members.filter(
            user=request.user, is_active=True, role__in=write_roles
        ).exists()


def get_member_role(request):
    """The requesting user's role in the request's store (None if not a member)."""
    store = getattr(request, 'store', None)
    if not store or not request.user or not request.user.is_authenticated:
        return None
    return store.members.filter(
        user=request.user, is_active=True
    ).values_list('role', flat=True).first()


def require_roles(request, roles):
    """Raise 403 unless the requesting user's role in this store is in `roles`."""
    if get_member_role(request) not in set(roles):
        raise exceptions.PermissionDenied("You don't have permission to do that in this store.")


class HasReadRole(permissions.BasePermission):
    """
    Optional read restriction. If a view defines `read_roles`, only members
    with one of those roles may use it at all (e.g. expenses and profit
    reports are not for cashiers). Views without `read_roles` are unaffected.
    """

    def has_permission(self, request, view):
        read_roles = getattr(view, 'read_roles', None)
        if not read_roles:
            return True
        store = getattr(request, 'store', None)
        if not store or not request.user or not request.user.is_authenticated:
            return False
        return store.members.filter(
            user=request.user, is_active=True, role__in=read_roles
        ).exists()


class TenantScopedViewSet(viewsets.ModelViewSet):
    """
    Base ViewSet for store-isolated entities.
    Automatically filters querysets by request.store and injects store context into serializers.
    """
    permission_classes = [permissions.IsAuthenticated, IsStoreMember, HasReadRole, HasWriteRole]

    def get_store(self):
        store = getattr(self.request, 'store', None)
        if store is None:
            raise exceptions.NotFound("Store context not resolved.")
        return store

    def get_queryset(self):
        return self.queryset.filter(store=self.get_store())

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['store'] = self.get_store()
        return context