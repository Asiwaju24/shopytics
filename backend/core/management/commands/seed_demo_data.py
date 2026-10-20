"""
Usage: python manage.py seed_demo_data [--reset]

Creates one demo store with a realistic Nigerian-SME product catalog,
suppliers, customers (some with overdue debt), and ~30 days of transaction
history with weekend uplift baked in — so the dashboard, top movers, and
AI recommendation feed all have real numbers to show instead of zeros.

Idempotent-ish: re-running without --reset just adds another day of
transactions on top of what's there; --reset wipes this demo store's
transactional data first (never touches other stores).
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    User, Store, StoreMembership, Supplier, Product, ProductSupplier,
    Customer, CustomerDebt, TransactionLog, Sale, StockMovement, Expense, DebtPayment,
)
from core.services.debts import record_payment
from core.services.errors import BusinessRuleError
from core.services.forecasting import generate_demand_forecasts, refresh_daily_summaries
from core.services.formatting import local_now
from core.services.recommendations import generate_recommendations_for_store
from core.services.sales import create_sale

DEMO_USERNAME = 'demo_owner'
DEMO_PASSWORD = 'DemoPassword123'
DEMO_STORE_NAME = "Ade's Provision Store"

PRODUCTS = [
    # name, category, cost, selling, stock, reorder_point, base_daily_velocity
    ("Rice 50kg Bag", "Grains", 38000, 45000, 22, 5, 1.5),
    ("Cooking Oil 5L", "Cooking Essentials", 7200, 8500, 3, 8, 2.0),   # intentionally critical
    ("Granulated Sugar 1kg", "Cooking Essentials", 900, 1200, 40, 15, 4.0),
    ("Indomie Carton (40pk)", "Instant Noodles", 6200, 7500, 6, 10, 3.5),  # intentionally low
    ("Milo 400g Tin", "Beverages", 2100, 2700, 30, 10, 2.5),
    ("Omo Detergent 1kg", "Household", 1400, 1850, 25, 10, 1.8),
    "SPLIT_MARKER",
    ("Peak Milk Tin", "Beverages", 850, 1100, 50, 15, 3.0),
    ("Bread (Family Size)", "Bakery", 700, 1000, 18, 10, 5.0),
    ("Coca-Cola Crate (24pk)", "Soft Drinks", 4200, 5200, 12, 8, 1.2),
]
PRODUCTS = [p for p in PRODUCTS if p != "SPLIT_MARKER"]

SUPPLIERS = [
    ("Lagos Foods Distribution Ltd", "Chidi Okafor", "+2348021110001", 3),
    ("Northern Grains Wholesale", "Amina Bello", "+2348021110002", 5),
]

CUSTOMERS = [
    ("Mrs. Funke Adeyemi", "+2348031110001", False),
    ("Chief Emeka Obi", "+2348031110002", True),   # overdue debt
    ("Ngozi Stores (retail)", "+2348031110003", True),  # overdue debt
    ("Tunde Balogun", "+2348031110004", False),
]


class Command(BaseCommand):
    help = "Seed a realistic demo store for frontend development."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help="Wipe this demo store's transactions/debts/recommendations before reseeding.",
        )
        parser.add_argument('--days', type=int, default=30, help="Days of sales history to generate.")

    def handle(self, *args, **options):
        random.seed(42)  # reproducible demo numbers across runs

        user, created = User.objects.get_or_create(
            username=DEMO_USERNAME,
            defaults={'email': 'demo@ipace.test', 'full_name': 'Ade Demo'},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()

        store, store_created = Store.objects.get_or_create(
            owner=user, name=DEMO_STORE_NAME,
            defaults={
                'whatsapp_number': '+2348012345678',
                'business_type': Store.BusinessType.GROCERY,
                'currency': Store.Currency.NGN,
                'daily_revenue_target': Decimal('180000.00'),
                'tax_rate_percent': Decimal('7.50'),
                'city': 'Lagos', 'country': 'Nigeria',
            },
        )
        StoreMembership.objects.get_or_create(
            user=user, store=store, defaults={'role': StoreMembership.Role.OWNER},
        )

        if options['reset']:
            TransactionLog.objects.filter(store=store).delete()
            Sale.objects.filter(store=store).delete()
            CustomerDebt.objects.filter(store=store).delete()
            StockMovement.objects.filter(store=store).delete()
            Expense.objects.filter(store=store).delete()
            store.recommendations.all().delete()
            store.forecasts.all().delete()
            self.stdout.write("Cleared existing demo sales/debts/expenses/stock history/recommendations.")

        suppliers = []
        for name, contact, phone, lead_time in SUPPLIERS:
            supplier, _ = Supplier.objects.get_or_create(
                store=store, name=name,
                defaults={'contact_person': contact, 'phone_number': phone, 'lead_time_days': lead_time},
            )
            suppliers.append(supplier)

        products = []
        for i, (name, category, cost, price, stock, reorder, velocity) in enumerate(PRODUCTS):
            product, product_created = Product.objects.get_or_create(
                store=store, sku=f"DEMO-{i+1:03d}",
                defaults={
                    'name': name, 'category': category,
                    'cost_price': Decimal(str(cost)), 'selling_price': Decimal(str(price)),
                    'current_stock': stock, 'reorder_point': reorder,
                },
            )
            if options['reset'] and not product_created:
                product.current_stock = stock
                product.save(update_fields=['current_stock', 'updated_at'])
            if product.current_stock > 0 and not StockMovement.objects.filter(product=product).exists():
                StockMovement.objects.create(
                    store=store, product=product, movement_type=StockMovement.Type.OPENING,
                    quantity_change=product.current_stock, stock_before=0,
                    stock_after=product.current_stock, reference_type='seed', note='Demo opening stock',
                )
            products.append((product, velocity))
            ProductSupplier.objects.get_or_create(
                product=product, supplier=suppliers[i % len(suppliers)],
                defaults={'is_preferred': True, 'last_cost_price': product.cost_price},
            )

        customers = []
        for name, phone, has_debt in CUSTOMERS:
            customer, _ = Customer.objects.get_or_create(store=store, name=name, defaults={'phone_number': phone})
            customers.append((customer, has_debt))

        for customer, has_debt in customers:
            if has_debt and not CustomerDebt.objects.filter(store=store, customer=customer, is_settled=False).exists():
                CustomerDebt.objects.create(
                    store=store, customer=customer,
                    amount=Decimal(str(random.choice([8000, 15000, 22000]))),
                    due_date=(timezone.now() - timezone.timedelta(days=random.randint(5, 40))).date(),
                )

        # Trailing sales history with weekend uplift (Fri/Sat busier — matches
        # the "predicted weekend demand" idea in the mockup, at least directionally).
        days = options['days']
        now = timezone.now()
        created_txns = 0
        for day_offset in range(days, -1, -1):
            day = now - timezone.timedelta(days=day_offset)
            is_weekend = day.weekday() in (4, 5)  # Fri, Sat
            for product, base_velocity in products:
                daily_units = max(0, round(random.gauss(
                    base_velocity * (1.4 if is_weekend else 1.0), base_velocity * 0.3
                )))
                if daily_units <= 0:
                    continue
                TransactionLog.objects.create(
                    store=store, product=product,
                    quantity_sold=daily_units,
                    unit_price=product.selling_price,
                    unit_cost=product.cost_price,
                    total_amount=product.selling_price * daily_units,
                    transaction_date=day.replace(
                        hour=random.randint(9, 20), minute=random.randint(0, 59)
                    ),
                    source=TransactionLog.Source.MANUAL,
                )
                created_txns += 1

        # A few real checkouts today (these go through the same service the
        # app uses: receipt numbers, stock deduction, credit sale -> debt).
        sellable = [p for p, _ in products if p.current_stock >= 10][:3]
        if sellable and not Sale.objects.filter(store=store).exists():
            try:
                create_sale(
                    store=store, user=user,
                    items=[{'product_id': p.id, 'quantity': 2, 'unit_price': None} for p in sellable],
                )
                create_sale(
                    store=store, user=user, customer=customers[0][0],
                    payment_method=Sale.PaymentMethod.TRANSFER,
                    items=[{'product_id': sellable[0].id, 'quantity': 1, 'unit_price': None}],
                )
                create_sale(
                    store=store, user=user, customer=customers[3][0],
                    payment_method=Sale.PaymentMethod.CREDIT,
                    items=[{'product_id': sellable[-1].id, 'quantity': 3, 'unit_price': None}],
                )
            except BusinessRuleError as exc:
                self.stdout.write(f"Skipped demo checkouts: {exc}")

        # One partial repayment so the debts screen has history.
        first_debt = CustomerDebt.objects.filter(store=store, is_settled=False, amount_paid=0, sale__isnull=True).first()
        if first_debt and not DebtPayment.objects.filter(store=store).exists():
            record_payment(first_debt, amount=Decimal('2000.00'), note='Part payment (demo)', user=user)

        if not Expense.objects.filter(store=store).exists():
            today = local_now(store).date()
            for days_ago, category, description, amount in [
                (25, Expense.Category.RENT, 'Shop rent', 150000),
                (20, Expense.Category.SALARIES, 'Shop assistant wages', 80000),
                (14, Expense.Category.UTILITIES, 'Electricity & data', 35000),
                (9, Expense.Category.TRANSPORT, 'Delivery of stock', 18000),
                (3, Expense.Category.MAINTENANCE, 'Freezer repair', 22000),
            ]:
                Expense.objects.create(
                    store=store, category=category, description=description,
                    amount=Decimal(str(amount)), expense_date=today - timedelta(days=days_ago),
                    recorded_by=user,
                )

        refresh_daily_summaries(store, local_now(store).date() - timedelta(days=days), local_now(store).date())
        generate_demand_forecasts(store)
        generate_recommendations_for_store(store)

        self.stdout.write(self.style.SUCCESS(
            f"\nDemo store ready: '{store.name}' (id={store.id})\n"
            f"Login  ->  username: {DEMO_USERNAME}" + (f"  password: {DEMO_PASSWORD}\n" if created else " (already existed — password unchanged)\n") +
            f"Seeded: {len(products)} products, {len(suppliers)} suppliers, {len(customers)} customers, "
            f"{created_txns} transactions over {days} days.\n"
            f"X-Store-ID header for API calls: {store.id}\n"
        ))
