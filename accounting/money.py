"""Integer-cent currency and micro-unit exchange rate arithmetic.

Strictly avoids floating-point arithmetic (`float`) for all monetary and
exchange-rate calculations to prevent rounding drift on financial statements.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

RATE_SCALE: int = 1_000_000  # 1.000000 -> 1_000_000 micros

CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "PHP": "₱",
    "JPY": "¥",
    "CAD": "C$",
    "AUD": "A$",
    "SGD": "S$",
    "CHF": "CHF ",
}


def parse_money_to_cents(value: str | int | Decimal) -> int:
    """Parse a user-entered monetary string or Decimal into integer cents.

    Floats are explicitly rejected to guarantee exact decimal precision.
    """
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid monetary amount.")
    if isinstance(value, float):
        raise TypeError("Float is forbidden for monetary amounts; pass str, int, or Decimal.")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        d = value
    else:
        cleaned = str(value).strip().replace(",", "")
        for sym in ("$", "€", "£", "₱", "¥", "C$", "A$", "S$", "CHF"):
            cleaned = cleaned.replace(sym, "")
        cleaned = cleaned.strip()
        if not cleaned:
            return 0
        try:
            d = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid monetary amount: {value!r}") from exc

    cents_dec = (d * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents_dec)


def parse_rate_to_micros(value: str | int | Decimal) -> int:
    """Parse an exchange rate string/Decimal into integer micro-units (x 1,000,000).

    Example: '1.250000' -> 1_250_000.
    """
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid exchange rate.")
    if isinstance(value, float):
        raise TypeError("Float is forbidden for exchange rates; pass str, int, or Decimal.")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("Exchange rate must be positive.")
        return value
    if isinstance(value, Decimal):
        d = value
    else:
        cleaned = str(value).strip().replace(",", "")
        if not cleaned:
            raise ValueError("Exchange rate cannot be empty.")
        try:
            d = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid exchange rate: {value!r}") from exc

    if d <= 0:
        raise ValueError("Exchange rate must be positive.")
    micros_dec = (d * Decimal(RATE_SCALE)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    result = int(micros_dec)
    if result <= 0:
        raise ValueError("Exchange rate must be positive.")
    return result


def convert_to_home(amount_foreign_cents: int, exchange_rate_micros: int) -> int:
    """Convert foreign integer cents to home integer cents using half-up integer rounding.

    Formula: round_half_up(amount_foreign_cents * exchange_rate_micros / 1_000_000)
    Uses pure integer arithmetic (zero float).
    """
    if not isinstance(amount_foreign_cents, int) or isinstance(amount_foreign_cents, bool):
        raise TypeError("amount_foreign_cents must be int")
    if not isinstance(exchange_rate_micros, int) or isinstance(exchange_rate_micros, bool):
        raise TypeError("exchange_rate_micros must be int")
    if exchange_rate_micros <= 0:
        raise ValueError("exchange_rate_micros must be > 0")

    if amount_foreign_cents == 0:
        return 0
    if exchange_rate_micros == RATE_SCALE:
        return amount_foreign_cents

    sign = -1 if amount_foreign_cents < 0 else 1
    abs_product = abs(amount_foreign_cents) * exchange_rate_micros
    rounded = (abs_product + (RATE_SCALE // 2)) // RATE_SCALE
    return sign * rounded


def format_money(cents: int, currency_code: str = "USD", show_symbol: bool = True) -> str:
    """Format integer cents into a human-readable currency string like '$1,234.56'."""
    if cents is None:
        cents = 0
    cents = int(cents)
    neg = cents < 0
    abs_cents = abs(cents)
    whole = abs_cents // 100
    frac = abs_cents % 100
    symbol = CURRENCY_SYMBOLS.get(currency_code, f"{currency_code} ") if show_symbol else ""
    sign = "-" if neg else ""
    return f"{sign}{symbol}{whole:,}.{frac:02d}"


def format_rate(rate_micros: int) -> str:
    """Format integer micro-units into a 6-decimal exchange rate string."""
    if rate_micros is None:
        rate_micros = RATE_SCALE
    rate_micros = int(rate_micros)
    whole = rate_micros // RATE_SCALE
    frac = abs(rate_micros) % RATE_SCALE
    return f"{whole}.{frac:06d}"


def calculate_line_amount(quantity: int, unit_rate_cents: int) -> int:
    """Exact integer multiplication for line quantity * unit rate (in cents)."""
    if not isinstance(quantity, int) or not isinstance(unit_rate_cents, int):
        raise TypeError("quantity and unit_rate_cents must be int")
    return quantity * unit_rate_cents


def apply_tax_bps(subtotal_cents: int, tax_bps: int) -> int:
    """Calculate tax in integer cents from basis points (100 bps = 1.00%)."""
    if tax_bps <= 0 or subtotal_cents == 0:
        return 0
    sign = -1 if subtotal_cents < 0 else 1
    abs_prod = abs(subtotal_cents) * tax_bps
    return sign * ((abs_prod + 5_000) // 10_000)
