"""Adding / removing staff."""
from core.models import StoreMembership, User
from .helpers import ShopTestCase

Role = StoreMembership.Role


class TeamTests(ShopTestCase):

    def test_owner_creates_new_staff_account(self):
        response = self.api('post', 'memberships/add-staff/', {
            'username': 'newcashier', 'role': 'CASHIER', 'full_name': 'New Cashier',
            'password': 'Str0ng-pass-123',
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['account_created'])
        user = User.objects.get(username='newcashier')
        self.assertTrue(user.check_password('Str0ng-pass-123'))
        self.assertIsNone(user.phone_number)
        self.assertTrue(StoreMembership.objects.filter(user=user, store=self.store, role=Role.CASHIER).exists())

    def test_existing_account_is_added_without_password(self):
        other = User.objects.create_user(username='existing', password='Str0ng-pass-123')
        response = self.api('post', 'memberships/add-staff/', {'username': 'existing', 'role': 'MANAGER'})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['account_created'])
        self.assertTrue(StoreMembership.objects.filter(user=other, store=self.store).exists())

    def test_unknown_username_without_password_rejected(self):
        response = self.api('post', 'memberships/add-staff/', {'username': 'ghost', 'role': 'CASHIER'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username='ghost').exists())

    def test_weak_password_rejected(self):
        response = self.api('post', 'memberships/add-staff/', {
            'username': 'weak', 'role': 'CASHIER', 'password': '12345678',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username='weak').exists())

    def test_duplicate_and_owner_role_rejected(self):
        self.assertEqual(
            self.api('post', 'memberships/add-staff/', {'username': 'cashier', 'role': 'CASHIER'}).status_code, 400
        )
        self.assertEqual(
            self.api('post', 'memberships/add-staff/', {'username': 'x', 'role': 'OWNER', 'password': 'Str0ng-pass-123'}).status_code, 400
        )

    def test_only_owner_can_manage_team(self):
        for user in (self.manager, self.cashier):
            self.client.force_authenticate(user=user)
            response = self.api('post', 'memberships/add-staff/', {
                'username': 'sneaky', 'role': 'CASHIER', 'password': 'Str0ng-pass-123',
            })
            self.assertEqual(response.status_code, 403)

    def test_remove_deactivates_and_readding_reactivates(self):
        membership = StoreMembership.objects.get(user=self.cashier, store=self.store)
        self.assertEqual(self.api('delete', f'memberships/{membership.id}/').status_code, 204)
        membership.refresh_from_db()
        self.assertFalse(membership.is_active)

        self.client.force_authenticate(user=self.cashier)
        self.assertEqual(self.api('get', 'products/').status_code, 403)   # lost access

        self.client.force_authenticate(user=self.owner)
        again = self.api('post', 'memberships/add-staff/', {'username': 'cashier', 'role': 'MANAGER'})
        self.assertEqual(again.status_code, 201, again.data)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.role, Role.MANAGER)

    def test_owner_cannot_be_removed_or_demoted(self):
        owner_membership = StoreMembership.objects.get(user=self.owner, store=self.store)
        self.assertEqual(self.api('delete', f'memberships/{owner_membership.id}/').status_code, 400)
        self.assertEqual(
            self.api('patch', f'memberships/{owner_membership.id}/', {'role': 'VIEWER'}).status_code, 400
        )

    def test_role_can_be_changed_but_not_to_owner(self):
        membership = StoreMembership.objects.get(user=self.viewer, store=self.store)
        ok = self.api('patch', f'memberships/{membership.id}/', {'role': 'ACCOUNTANT'})
        self.assertEqual(ok.status_code, 200, ok.data)
        bad = self.api('patch', f'memberships/{membership.id}/', {'role': 'OWNER'})
        self.assertEqual(bad.status_code, 400)

    def test_register_without_phone_twice_works(self):
        from django.core.cache import cache
        cache.clear()   # auth endpoints are rate-limited per minute
        for name in ('reg1', 'reg2'):
            response = self.client.post('/api/auth/register/', {
                'username': name, 'password': 'Str0ng-pass-123', 'phone_number': '',
            }, format='json')
            self.assertEqual(response.status_code, 201, response.data)
