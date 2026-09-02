from decimal import Decimal, ROUND_HALF_UP

PAISE_PER_RUPEE = 100
GST_RATE_PCT = 18


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def bps_of(amount_paise: int, bps: int) -> int:
    return round_half_up(Decimal(amount_paise) * Decimal(bps) / Decimal(10_000))


def gst_on(fee_paise: int, rate_pct: int = GST_RATE_PCT) -> int:
    return round_half_up(Decimal(fee_paise) * Decimal(rate_pct) / Decimal(100))


def to_paise(rupee_value) -> int:
    return round_half_up(Decimal(str(rupee_value)) * PAISE_PER_RUPEE)


def rupees(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    magnitude = abs(paise)
    whole, frac = divmod(magnitude, PAISE_PER_RUPEE)
    return f"{sign}₹{whole:,}.{frac:02d}"
