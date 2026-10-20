"""
iPace SME Business Co-Pilot — Auth Views
==========================================
Registration, login/refresh (SimpleJWT, throttled), logout (blacklists the
refresh token), and "who am I".
"""

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.exceptions import TokenError
from drf_spectacular.utils import extend_schema

from ..serializers import (
    RegisterSerializer, UserSerializer, StoreMembershipSerializer,
    UpdateProfileSerializer, ChangePasswordSerializer, LogoutSerializer, MeResponseSerializer,
)
from ..models import StoreMembership


class RegisterView(generics.CreateAPIView):
    """POST /api/auth/register/ — create an account and return JWTs immediately."""
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer
    throttle_scope = 'auth'

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                'user': UserSerializer(user).data,
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(TokenObtainPairView):
    """POST /api/auth/login/ — thin wrapper over SimpleJWT's view, just to attach throttling."""
    throttle_scope = 'auth'


class RefreshView(TokenRefreshView):
    """POST /api/auth/refresh/ — same as above."""
    throttle_scope = 'auth'


class LogoutView(APIView):
    """
    POST /api/auth/logout/ — blacklists the given refresh token so it (and
    any access token later minted from it) can no longer be used. Requires
    BLACKLIST_AFTER_ROTATION / the token_blacklist app (see settings.py).
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'auth'

    @extend_schema(request=LogoutSerializer, responses={205: None, 400: None})
    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({'detail': "'refresh' is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            return Response({'detail': 'Invalid or already-invalidated token.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(APIView):
    """
    GET   /api/auth/me/ — the authenticated user's profile plus every store
    they belong to, so the frontend can populate the store switcher without
    a second round trip.
    PATCH /api/auth/me/ — partial update of your own profile.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=MeResponseSerializer)
    def get(self, request):
        memberships = StoreMembership.objects.filter(
            user=request.user, is_active=True, store__is_active=True
        ).select_related('store')

        return Response({
            'user': UserSerializer(request.user).data,
            'memberships': StoreMembershipSerializer(memberships, many=True).data,
        })

    @extend_schema(request=UpdateProfileSerializer, responses=UserSerializer)
    def patch(self, request):
        serializer = UpdateProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    """POST /api/auth/change-password/ — requires the current password."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'auth'

    @extend_schema(request=ChangePasswordSerializer, responses={200: None})
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'detail': 'Password updated.'}, status=status.HTTP_200_OK)
