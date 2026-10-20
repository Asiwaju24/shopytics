import uuid
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

# Relative imports from parent core app
from ..models import Store, StoreMembership
from ..middleware import StoreContextMiddleware

User = get_user_model()


class StoreContextMiddlewareTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = StoreContextMiddleware(get_response=lambda r: None)

        self.user_a = User.objects.create_user(username='user_a', password='password123')
        self.user_b = User.objects.create_user(username='user_b', password='password123')

        self.store_a = Store.objects.create(name="Store Alpha", owner=self.user_a)
        self.store_b = Store.objects.create(name="Store Beta", owner=self.user_b)

        StoreMembership.objects.create(user=self.user_a, store=self.store_a, is_active=True)
        StoreMembership.objects.create(user=self.user_b, store=self.store_b, is_active=True)

    def test_header_resolves_store(self):
        request = self.factory.get('/api/products/', HTTP_X_STORE_ID=str(self.store_a.id))
        request.user = self.user_a
        self.middleware(request)
        self.assertEqual(request.store, self.store_a)

    def test_query_param_resolves_store(self):
        request = self.factory.get(f'/api/products/?store_id={self.store_a.id}')
        request.user = self.user_a
        self.middleware(request)
        self.assertEqual(request.store, self.store_a)

    def test_unauthorized_store_access_returns_none(self):
        request = self.factory.get('/api/products/', HTTP_X_STORE_ID=str(self.store_b.id))
        request.user = self.user_a
        self.middleware(request)
        self.assertIsNone(request.store)

    def test_fallback_to_user_active_membership(self):
        request = self.factory.get('/api/products/')
        request.user = self.user_a
        self.middleware(request)
        self.assertEqual(request.store, self.store_a)