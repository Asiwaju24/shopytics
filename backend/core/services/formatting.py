"""Small shared helpers: money formatting, store-timezone day boundaries, WhatsApp links."""
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone

from .errors import BusinessRuleError

CURRENCY_SYMBOLS = {
    'NGN': '₦',
    'USD': '$',
    'GHS': '₵',
    'KES': 'KSh ',
    'ZAR': 'R',
}

# Country calling codes used to turn a local number (0803...) into an
# international one for wa.me links.
CALLING_CODES = {
    'NGN': '234',
    'GHS': '233',
    'KES': '254',
    'ZAR': '27',
    'USD': '1',
}

MAX_REPORT_DAYS = 366


def currency_symbol(currency):
    return CURRENCY_SYMBOLS.get(currency, f"{currency} ")


def format_money(amount, currency='NGN'):
    amount = Decimal(str(amount or 0))
    return f"{currency_symbol(currency)}{amount:,.0f}"


def store_tz(store):
    """The store's own timezone; falls back to UTC if the stored name is invalid."""
    try:
        return ZoneInfo(store.timezone)
    except Exception:
        return ZoneInfo('UTC')


def local_now(store):
    return timezone.now().astimezone(store_tz(store))


def day_start(store, local_date):
    """Aware datetime for 00:00 of `local_date` in the store's timezone."""
    return datetime.combine(local_date, time.min, tzinfo=store_tz(store))


def today_bounds(store):
    """(start, end) of the store's current local day as aware datetimes."""
    today = local_now(store).date()
    start = day_start(store, today)
    return start, day_start(store, today + timedelta(days=1))


def parse_date(raw, field_name):
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), '%Y-%m-%d').date()
    except ValueError:
        raise BusinessRuleError(f"'{field_name}' must be a date in YYYY-MM-DD format.")


def resolve_period(store, raw_from=None, raw_to=None, default_days=30):
    """
    Turn optional ?date_from / ?date_to strings into
    (date_from, date_to, start_dt, end_dt) in the store's timezone.
    `end_dt` is exclusive (00:00 of the day after `date_to`).
    """
    today = local_now(store).date()
    date_to = parse_date(raw_to, 'date_to') or today
    date_from = parse_date(raw_from, 'date_from') or (date_to - timedelta(days=default_days - 1))
    if date_from > date_to:
        raise BusinessRuleError("'date_from' cannot be after 'date_to'.")
    if (date_to - date_from).days + 1 > MAX_REPORT_DAYS:
        raise BusinessRuleError(f"Please choose a period of at most {MAX_REPORT_DAYS} days.")
    return date_from, date_to, day_start(store, date_from), day_start(store, date_to + timedelta(days=1))


def whatsapp_link(phone, message, currency='NGN'):
    """
    Build a wa.me deep link. Accepts "+234 803 ...", "0803...", "234803...".
    Returns '' when the number has too few digits to be usable.
    """
    from urllib.parse import quote

    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = CALLING_CODES.get(currency, '234') + digits[1:]
    if len(digits) < 8:
        return ''
    return f"https://wa.me/{digits}?text={quote(message)}"
