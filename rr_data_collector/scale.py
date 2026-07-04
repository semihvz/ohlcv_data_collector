from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .config import PRICE_SCALE, VOLUME_SCALE


def decimal_to_scaled(value: str | int | float | Decimal, scale: int) -> int:
    decimal_value = Decimal(str(value))
    return int((decimal_value * scale).to_integral_value(rounding=ROUND_HALF_UP))


def price_to_i(value: str | int | float | Decimal) -> int:
    return decimal_to_scaled(value, PRICE_SCALE)


def volume_to_i(value: str | int | float | Decimal) -> int:
    return decimal_to_scaled(value, VOLUME_SCALE)


def mul_ratio_i(value: int, numerator: int, denominator: int) -> int:
    return int((Decimal(value) * Decimal(numerator) / Decimal(denominator)).to_integral_value(rounding=ROUND_HALF_UP))


def pct_bp(numerator: int, denominator: int) -> int:
    if denominator == 0:
        return 0
    return int((Decimal(numerator) * Decimal(10_000) / Decimal(denominator)).to_integral_value(rounding=ROUND_HALF_UP))
