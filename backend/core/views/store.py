"""
iPace SME Business Co-Pilot — Store Onboarding & Membership
=============================================================
Unlike everything in `resources.py`, `StoreViewSet` doesn't operate inside
an existing store's context (there's no store yet when you're creating
one), so it deliberately does NOT extend `TenantScopedViewSet`.
`StoreMembershipViewSet` DOES need store context (X-Store-ID) since it
manages membership *within* an already-resolved store.
"""

from rest_framework import decorators, permissions, status, viewsets
from rest_framework.response import Response

from .base import TenantScopedViewSet
from ..models import Store, StoreMembership
from ..serializers import StoreSerializer, StoreMembershipSerializer, AddStaffSerializer
from ..services.errors import BusinessRuleError
from ..services.team import add_staff


class StoreViewSet(viewsets.ModelViewSet):
    """
    /api/stores/ — list/create the stores the current user belongs to.
    Creating a store makes the creator its OWNER (see StoreSerializer.create).
    Only the owner may update or delete their store.
    """
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Store.objects.filter(
            members__user=self.request.user,
            members__is_active=True,
            is_active=True,
        ).distinct()

    def perform_update(self, serializer):
        store = self.get_object()
        if store.owner_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only the store owner can edit store settings.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.owner_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only the store owner can remove a store.")
        instance.soft_delete()


class StoreMembershipViewSet(TenantScopedViewSet):
    """
    /api/memberships/ — manage who belongs to the current store context
    (X-Store-ID header). Listing is open to any active member; inviting,
    changing roles, or removing members is OWNER-only.
    """
    queryset = StoreMembership.objects.select_related('user').all()
    serializer_class = StoreMembershipSerializer
    write_roles = {StoreMembership.Role.OWNER}

    def _protect_owner(self, membership):
        if membership.role == StoreMembership.Role.OWNER or membership.user_id == self.get_store().owner_id:
            raise BusinessRuleError("The store owner's access cannot be changed or removed.")

    def perform_update(self, serializer):
        self._protect_owner(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        # Deactivate rather than delete so the person's past sales stay attributed.
        self._protect_owner(instance)
        instance.is_active = False
        instance.save(update_fields=['is_active'])

    @decorators.action(detail=False, methods=['post'], url_path='add-staff', url_name='add-staff')
    def add_staff_member(self, request):
        """
        POST /api/memberships/add-staff/ — give someone access to this store.
        Existing username -> added to the store. Unknown username + password ->
        a new account is created for them.
        """
        serializer = AddStaffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        membership, created = add_staff(
            self.get_store(),
            username=data['username'], role=data['role'],
            full_name=data.get('full_name', ''), email=data.get('email', ''),
            phone_number=data.get('phone_number', ''), password=data.get('password', ''),
        )
        return Response(
            {'membership': StoreMembershipSerializer(membership).data, 'account_created': created},
            status=status.HTTP_201_CREATED,
        )
