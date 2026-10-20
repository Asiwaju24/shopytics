import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        # ── Store: tax + receipt footer ────────────────────────────────────
        migrations.AddField(
            model_name='store',
            name='tax_rate_percent',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'),
                help_text='VAT / sales tax added on top of every sale (0 for none).',
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name='store',
            name='receipt_footer',
            field=models.CharField(
                blank=True, default='Thank you for shopping with us!', max_length=255,
            ),
        ),

        # ── Sale ───────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='Sale',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('receipt_seq', models.PositiveIntegerField()),
                ('receipt_number', models.CharField(max_length=32)),
                ('payment_method', models.CharField(choices=[('CASH', 'Cash'), ('TRANSFER', 'Bank Transfer'), ('CARD', 'Card / POS'), ('CREDIT', 'On Credit')], default='CASH', max_length=20)),
                ('status', models.CharField(choices=[('COMPLETED', 'Completed'), ('VOIDED', 'Voided')], default='COMPLETED', max_length=20)),
                ('subtotal', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
                ('tax_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
                ('total_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
                ('amount_paid', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
                ('notes', models.TextField(blank=True)),
                ('sold_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('voided_at', models.DateTimeField(blank=True, null=True)),
                ('void_reason', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cashier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_made', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales', to='core.customer')),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales', to='core.store')),
                ('voided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_voided', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-sold_at', '-receipt_seq'],
                'unique_together': {('store', 'receipt_seq')},
                'indexes': [
                    models.Index(fields=['store', 'sold_at'], name='sale_store_sold_idx'),
                    models.Index(fields=['store', 'status'], name='sale_store_status_idx'),
                ],
            },
        ),

        # ── TransactionLog: link to Sale + voided flag ─────────────────────
        migrations.AddField(
            model_name='transactionlog',
            name='sale',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lines', to='core.sale'),
        ),
        migrations.AddField(
            model_name='transactionlog',
            name='is_voided',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddIndex(
            model_name='transactionlog',
            index=models.Index(fields=['store', 'is_voided', 'transaction_date'], name='txn_store_live_date_idx'),
        ),

        # ── CustomerDebt: link to the credit sale that created it ──────────
        migrations.AddField(
            model_name='customerdebt',
            name='sale',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='debts', to='core.sale'),
        ),

        # ── StockMovement ──────────────────────────────────────────────────
        migrations.CreateModel(
            name='StockMovement',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('movement_type', models.CharField(choices=[('OPENING', 'Opening Stock'), ('SALE', 'Sale'), ('SALE_VOID', 'Sale Voided (Restocked)'), ('PURCHASE', 'Purchase Received'), ('ADJUSTMENT', 'Manual Adjustment'), ('DAMAGE', 'Damaged'), ('EXPIRED', 'Expired'), ('COUNT', 'Stock Count Correction')], max_length=20)),
                ('quantity_change', models.IntegerField()),
                ('stock_before', models.IntegerField()),
                ('stock_after', models.IntegerField()),
                ('reference_type', models.CharField(blank=True, max_length=30)),
                ('reference_id', models.CharField(blank=True, max_length=64)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_movements', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='movements', to='core.product')),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stock_movements', to='core.store')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['store', 'created_at'], name='stockmv_store_created_idx'),
                    models.Index(fields=['product', 'created_at'], name='stockmv_product_created_idx'),
                ],
            },
        ),

        # ── DebtPayment ────────────────────────────────────────────────────
        migrations.CreateModel(
            name='DebtPayment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('method', models.CharField(choices=[('CASH', 'Cash'), ('TRANSFER', 'Bank Transfer'), ('CARD', 'Card / POS'), ('OTHER', 'Other')], default='CASH', max_length=20)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('paid_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('debt', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='core.customerdebt')),
                ('received_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='debt_payments_received', to=settings.AUTH_USER_MODEL)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='debt_payments', to='core.store')),
            ],
            options={
                'ordering': ['-paid_at'],
                'indexes': [
                    models.Index(fields=['store', 'paid_at'], name='debtpay_store_paid_idx'),
                ],
            },
        ),

        # ── Expense ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='Expense',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('category', models.CharField(choices=[('RENT', 'Rent'), ('SALARIES', 'Salaries & Wages'), ('UTILITIES', 'Utilities (power, water, data)'), ('TRANSPORT', 'Transport & Delivery'), ('MARKETING', 'Marketing'), ('SUPPLIES', 'Supplies & Packaging'), ('MAINTENANCE', 'Repairs & Maintenance'), ('TAXES', 'Taxes & Licences'), ('OTHER', 'Other')], default='OTHER', max_length=30)),
                ('description', models.CharField(max_length=255)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('expense_date', models.DateField(db_index=True)),
                ('payment_method', models.CharField(choices=[('CASH', 'Cash'), ('TRANSFER', 'Bank Transfer'), ('CARD', 'Card / POS')], default='CASH', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('recorded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses_recorded', to=settings.AUTH_USER_MODEL)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='expenses', to='core.store')),
            ],
            options={
                'ordering': ['-expense_date', '-created_at'],
                'indexes': [
                    models.Index(fields=['store', 'expense_date'], name='expense_store_date_idx'),
                ],
            },
        ),
    ]
