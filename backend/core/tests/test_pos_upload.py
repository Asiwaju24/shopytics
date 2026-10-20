"""
iPace SME Business Co-Pilot — POS Upload Tests
=================================================
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import User, Store, StoreMembership, Product, TransactionLog


class POSUploadTestCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='posowner', password='password12345')
        self.store = Store.objects.create(
            owner=self.owner, name='POS Store', whatsapp_number='+2348000000006',
        )
        StoreMembership.objects.create(user=self.owner, store=self.store, role=StoreMembership.Role.OWNER)
        self.product = Product.objects.create(
            store=self.store, sku='SKU-1', name='Widget',
            cost_price=50, selling_price=100, current_stock=50,
        )
        self.client.force_authenticate(user=self.owner)

    def _csv(self, content):
        return SimpleUploadedFile('sales.csv', content.encode('utf-8'), content_type='text/csv')

    def test_dry_run_parses_without_committing(self):
        csv_content = (
            "SKU,Qty_Sold,Price,Date\r\n"
            "SKU-1,3,100,2026-01-01\r\n"
        )
        response = self.client.post(
            reverse('transaction-upload'),
            {'file': self._csv(csv_content), 'source': 'CSV_IMPORT', 'dry_run': 'true'},
            format='multipart',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['valid_rows'], 1)
        self.assertEqual(response.data['created_transactions'], 0)
        self.assertEqual(TransactionLog.objects.count(), 0)

    def test_commit_creates_transactions(self):
        csv_content = (
            "SKU,Qty_Sold,Price,Date\r\n"
            "SKU-1,3,100,2026-01-01\r\n"
            "SKU-1,2,100,2026-01-02\r\n"
        )
        response = self.client.post(
            reverse('transaction-upload'),
            {'file': self._csv(csv_content), 'source': 'CSV_IMPORT', 'dry_run': 'false'},
            format='multipart',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['created_transactions'], 2)
        self.assertEqual(TransactionLog.objects.filter(store=self.store).count(), 2)

    def test_unknown_product_is_reported_not_silently_dropped(self):
        csv_content = (
            "SKU,Qty_Sold,Price,Date\r\n"
            "UNKNOWN-SKU,1,100,2026-01-01\r\n"
        )
        response = self.client.post(
            reverse('transaction-upload'),
            {'file': self._csv(csv_content), 'source': 'CSV_IMPORT', 'dry_run': 'true'},
            format='multipart',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['invalid_rows'], 1)
        self.assertEqual(len(response.data['issues']), 1)

    def test_missing_price_is_estimated_from_store_average(self):
        csv_content = (
            "SKU,Qty_Sold,Price,Date\r\n"
            "SKU-1,1,,2026-01-01\r\n"
        )
        response = self.client.post(
            reverse('transaction-upload'),
            {'file': self._csv(csv_content), 'source': 'CSV_IMPORT', 'dry_run': 'true'},
            format='multipart',
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['valid_rows'], 1)
        self.assertEqual(response.data['estimated_prices'], 1)
