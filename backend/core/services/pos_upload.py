"""
iPace SME Business Co-Pilot — POS Upload Cleaner
===================================================
Real implementation of the "1-Click POS Data Clean" flow: parses an
uploaded CSV/XLSX file, maps common header aliases onto our schema,
repairs a few common messy-export problems, and (outside dry-run)
commits clean rows as `TransactionLog` entries.

This intentionally does NOT try to be a universal importer — it covers
the header variations you'll actually see from Nigerian SME POS/Excel
exports. Add aliases to HEADER_ALIASES as new formats show up.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

import openpyxl
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import StockMovement, TransactionLog
from .errors import BusinessRuleError
from .inventory import apply_stock_change, lock_products

# Maps our canonical column name -> the header spellings we'll accept.
HEADER_ALIASES = {
    'sku': {'sku', 'product_sku', 'item_code', 'itemcode', 'code'},
    'product_name': {'product', 'product_name', 'item', 'item_name', 'description'},
    'quantity_sold': {'qty', 'qty_sold', 'quantity', 'quantity_sold', 'units', 'units_sold'},
    'unit_price': {'price', 'unit_price', 'selling_price', 'unitprice', 'sale_price'},
    'transaction_date': {'date', 'transaction_date', 'sale_date', 'sold_at', 'datetime'},
    'external_id': {'ref', 'reference', 'transaction_id', 'receipt_no', 'external_id'},
}


def _normalize_header(raw):
    return str(raw).strip().lower().replace(' ', '_').replace('-', '_')


def _build_column_map(headers):
    """Map canonical field name -> column index, from whatever headers the file has."""
    normalized = [_normalize_header(h) for h in headers]
    column_map = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for idx, h in enumerate(normalized):
            if h in aliases:
                column_map[canonical] = idx
                break
    return column_map


def _read_rows(uploaded_file):
    """Yields (headers, row) for either CSV or XLSX, sniffing by extension."""
    name = uploaded_file.name.lower()
    if name.endswith('.csv'):
        content = uploaded_file.read().decode('utf-8-sig', errors='replace')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return [], []
        return rows[0], rows[1:]

    # .xlsx via openpyxl (legacy binary .xls is rejected by the upload serializer).
    workbook = openpyxl.load_workbook(uploaded_file, data_only=True, read_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers = [str(h) if h is not None else '' for h in rows[0]]
    body = [[('' if c is None else c) for c in r] for r in rows[1:]]
    return headers, body


def _parse_decimal(raw):
    if raw is None or raw == '':
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw))
    cleaned = str(raw).replace(',', '').replace('₦', '').replace('$', '').strip()
    if cleaned in ('', '-'):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_int(raw):
    dec = _parse_decimal(raw)
    return int(dec) if dec is not None else None


def _parse_date(raw):
    if raw is None or raw == '':
        return None
    if isinstance(raw, datetime):
        return raw if timezone.is_aware(raw) else timezone.make_aware(raw)
    parsed = parse_datetime(str(raw))
    if parsed:
        return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y'):
        try:
            dt = datetime.strptime(str(raw).strip(), fmt)
            return timezone.make_aware(dt)
        except ValueError:
            continue
    return None


@dataclass
class RowIssue:
    row_number: int
    reason: str


@dataclass
class UploadResult:
    rows_parsed: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    estimated_prices: int = 0
    duplicate_rows: int = 0
    created_transactions: int = 0
    issues: list = field(default_factory=list)


def process_pos_upload(store, uploaded_file, source, dry_run=True, update_stock=False, user=None):
    """
    Parses `uploaded_file`, maps columns, validates/repairs rows, and
    (when `dry_run` is False) commits valid rows as `TransactionLog`
    entries. Products are matched by SKU first, then by exact name within
    the store; rows referencing an unknown product are skipped and
    reported rather than silently dropped. Rows whose reference/receipt
    number was already imported are skipped as duplicates, so uploading the
    same file twice can't double-count sales.

    `update_stock=True` also deducts the imported quantities from current
    stock (via the stock ledger). Leave it off for historical data.
    """
    result = UploadResult()

    headers, body_rows = _read_rows(uploaded_file)
    column_map = _build_column_map(headers)

    required = {'quantity_sold', 'unit_price'}
    missing = required - column_map.keys()
    if missing:
        result.issues.append(RowIssue(
            row_number=0,
            reason=f"Could not find required column(s): {', '.join(sorted(missing))}. "
                   f"Detected headers: {headers}",
        ))
        return result

    if 'sku' not in column_map and 'product_name' not in column_map:
        result.issues.append(RowIssue(
            row_number=0,
            reason="Could not find a product identifier column (sku or product name).",
        ))
        return result

    # Cache products for this store to avoid a query per row.
    active_products = list(store.products.filter(is_active=True))
    products_by_sku = {p.sku.lower(): p for p in active_products}
    products_by_name = {p.name.lower(): p for p in active_products}
    existing_refs = set(
        TransactionLog.objects.filter(store=store).exclude(external_id='')
        .values_list('external_id', flat=True)
    )
    seen_refs = set()

    # Store-average selling price, used to estimate genuinely missing prices
    # (never to silently override a price that was actually present).
    active_prices = [p.selling_price for p in products_by_sku.values() if p.selling_price]
    store_avg_price = (sum(active_prices) / len(active_prices)) if active_prices else None

    to_create = []
    for i, row in enumerate(body_rows, start=2):  # row 1 is the header
        result.rows_parsed += 1

        def cell(field_name):
            idx = column_map.get(field_name)
            return row[idx] if idx is not None and idx < len(row) else None

        sku = str(cell('sku') or '').strip()
        name = str(cell('product_name') or '').strip()
        product = None
        if sku:
            product = products_by_sku.get(sku.lower())
        if product is None and name:
            product = products_by_name.get(name.lower())

        if product is None:
            result.invalid_rows += 1
            result.issues.append(RowIssue(i, f"Unknown product (sku={sku!r}, name={name!r}) — not in catalog."))
            continue

        quantity = _parse_int(cell('quantity_sold'))
        if quantity is None or quantity <= 0:
            result.invalid_rows += 1
            result.issues.append(RowIssue(i, "Missing or non-positive quantity."))
            continue

        price = _parse_decimal(cell('unit_price'))
        if price is None or price < 0:
            if store_avg_price is not None:
                price = store_avg_price
                result.estimated_prices += 1
            else:
                result.invalid_rows += 1
                result.issues.append(RowIssue(i, "Missing price and no store average available to estimate from."))
                continue

        txn_date = _parse_date(cell('transaction_date')) or timezone.now()
        external_id = str(cell('external_id') or '').strip()

        if external_id:
            if external_id in existing_refs or external_id in seen_refs:
                result.duplicate_rows += 1
                result.issues.append(RowIssue(i, f"Reference {external_id!r} was already imported — skipped."))
                continue
            seen_refs.add(external_id)

        result.valid_rows += 1
        to_create.append(TransactionLog(
            store=store,
            product=product,
            quantity_sold=quantity,
            unit_price=price,
            unit_cost=product.cost_price,
            total_amount=Decimal(quantity) * price,
            currency=store.currency,
            transaction_date=txn_date,
            source=source,
            external_id=external_id,
        ))

    if not dry_run and to_create:
        with transaction.atomic():
            needed = {}
            for txn in to_create:
                needed[txn.product_id] = needed.get(txn.product_id, 0) + txn.quantity_sold

            locked = {}
            if update_stock:
                locked = lock_products(store, needed.keys())
                for product_id, qty in needed.items():
                    product = locked[product_id]
                    if qty > product.current_stock:
                        raise BusinessRuleError(
                            f"Cannot update stock: the file sells {qty} of {product.name} but only "
                            f"{product.current_stock} are in stock. Correct the stock first, or import "
                            f"without 'update stock'."
                        )

            created = TransactionLog.objects.bulk_create(to_create)
            result.created_transactions = len(created)

            for product_id, qty in needed.items():
                if update_stock:
                    apply_stock_change(
                        locked[product_id], -qty, StockMovement.Type.SALE, user=user,
                        note='POS file import', reference_type='import',
                    )

    return result
