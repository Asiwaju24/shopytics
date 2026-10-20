"""
iPace SME Business Co-Pilot — Auth Endpoint Tests
====================================================
"""
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import User


class AuthEndpointTestCase(APITestCase):
    def setUp(self):
        # Throttling shares Django's default cache across tests in this
        # process — clear it before each test so login-attempt counts from
        # one test never bleed into another regardless of run order.
        cache.clear()
    def test_register_creates_user_and_returns_tokens(self):
        response = self.client.post(reverse('auth-register'), {
            'username': 'newowner',
            'email': 'newowner@example.com',
            'password': 'a-strong-password123',
            'full_name': 'New Owner',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['username'], 'newowner')
        self.assertTrue(User.objects.filter(username='newowner').exists())

    def test_register_rejects_duplicate_username(self):
        User.objects.create_user(username='taken', password='password12345')
        response = self.client.post(reverse('auth-register'), {
            'username': 'taken',
            'password': 'another-password123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_returns_jwt_pair(self):
        User.objects.create_user(username='loginuser', password='correct-password123')
        response = self.client.post(reverse('auth-login'), {
            'username': 'loginuser',
            'password': 'correct-password123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_register_rejects_password_too_similar_to_username(self):
        response = self.client.post(reverse('auth-register'), {
            'username': 'similaruser',
            'password': 'similaruser123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.data)

    def test_register_rejects_common_password(self):
        response = self.client.post(reverse('auth-register'), {
            'username': 'someoneelse',
            'password': 'password123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.data)

    def test_logout_blacklists_refresh_token(self):
        User.objects.create_user(username='logoutuser', password='a-strong-password123')
        login = self.client.post(reverse('auth-login'), {
            'username': 'logoutuser', 'password': 'a-strong-password123',
        }, format='json')
        access, refresh = login.data['access'], login.data['refresh']

        logout_response = self.client.post(
            reverse('auth-logout'), {'refresh': refresh}, format='json',
            HTTP_AUTHORIZATION=f'Bearer {access}',
        )
        self.assertEqual(logout_response.status_code, status.HTTP_205_RESET_CONTENT)

        # The blacklisted refresh token can no longer mint a new access token.
        refresh_response = self.client.post(reverse('auth-refresh'), {'refresh': refresh}, format='json')
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_is_throttled_after_repeated_attempts(self):
        User.objects.create_user(username='throttleuser', password='a-strong-password123')
        # Default 'auth' scope allows 10/min — the 11th hit in the same
        # minute (from the same test-client IP) should be throttled.
        for _ in range(10):
            self.client.post(reverse('auth-login'), {
                'username': 'throttleuser', 'password': 'wrong',
            }, format='json')
        response = self.client.post(reverse('auth-login'), {
            'username': 'throttleuser', 'password': 'wrong',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_login_rejects_wrong_password(self):
        User.objects.create_user(username='loginuser2', password='correct-password123')
        response = self.client.post(reverse('auth-login'), {
            'username': 'loginuser2',
            'password': 'wrong-password',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_requires_authentication(self):
        response = self.client.get(reverse('auth-me'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_profile_and_memberships_with_valid_token(self):
        User.objects.create_user(username='meuser', password='correct-password123')
        login = self.client.post(reverse('auth-login'), {
            'username': 'meuser', 'password': 'correct-password123',
        }, format='json')
        access = login.data['access']

        response = self.client.get(
            reverse('auth-me'), HTTP_AUTHORIZATION=f'Bearer {access}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['user']['username'], 'meuser')
        self.assertEqual(response.data['memberships'], [])
