"""
iPace SME Business Co-Pilot — Store Context Middleware
======================================================
Resolves and attaches `request.store` to incoming requests based on:
1. `X-Store-ID` HTTP header
2. `store_id` URL query parameter
3. User's active default store membership

If a store header/param is provided but the user lacks access, attaches `None`
so permission classes (`IsStoreMember`) handle rejection gracefully.

NOTE: Django middleware's `process_request` runs *before* DRF performs
authentication (token/JWT/session-via-DRF/`force_authenticate` in tests all
resolve `request.user` later, inside `APIView.dispatch() -> initial() ->
perform_authentication()`). So for any request authenticated purely at the
DRF layer, `request.user` is still `AnonymousUser` here and this middleware
cannot resolve a store. It still handles plain Django-session-authenticated
requests (e.g. the admin, or browsable API login). `IsStoreMember` (see
`core/views/base.py`) re-resolves the store after DRF authentication has
completed, using `resolve_store_for_user` below, so DRF-authenticated
requests are still scoped correctly.
"""

import uuid
from django.utils.deprecation import MiddlewareMixin
from .models import Store, StoreMembership


def resolve_store_for_user(user, raw_store_id):
    """
    Resolve the `Store` a user should be scoped to, given an optional raw
    store id taken from a header or query param. Shared by
    `StoreContextMiddleware` and `IsStoreMember` so both apply identical
    rules regardless of when in the request lifecycle `user` became known.

    Returns `None` if no store could be resolved (invalid id, no access,
    inactive store/membership, or no membership at all).
    """
    if raw_store_id:
        try:
            store_uuid = uuid.UUID(str(raw_store_id))
        except (ValueError, AttributeError, TypeError):
            return None

        membership = StoreMembership.objects.filter(
            user=user,
            store_id=store_uuid,
            is_active=True
        ).select_related('store').first()

        if membership and membership.store.is_active:
            return membership.store
        return None

    # Fallback: the user's primary/most-recently-joined active store membership
    membership = StoreMembership.objects.filter(
        user=user,
        is_active=True,
        store__is_active=True
    ).select_related('store').order_by('-joined_at').first()

    return membership.store if membership else None


class StoreContextMiddleware(MiddlewareMixin):
    """
    Middleware that extracts store context and attaches it to `request.store`.
    """

    # Endpoints that do NOT require store context resolution
    EXCLUDED_PREFIXES = (
        '/admin/',
        '/api/auth/',
        '/api/schema/',
        '/api/docs/',
        '/health/',
    )

    def process_request(self, request):
        request.store = None

        # 1. Pass through public / excluded endpoints
        if any(request.path.startswith(prefix) for prefix in self.EXCLUDED_PREFIXES):
            return None

        # 2. Require an authenticated user for store-scoped paths
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return None

        # 3. Extract store ID from Header or Query Param
        raw_store_id = request.headers.get('X-Store-ID') or request.GET.get('store_id')

        # Attach resolved store instance to request
        request.store = resolve_store_for_user(request.user, raw_store_id)
        return None
