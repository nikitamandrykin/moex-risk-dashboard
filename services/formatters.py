from __future__ import annotations

from typing import Any
import math


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def fmt_rate(value: Any) -> str:
    """Format a market-risk rate. Fractions (0.1) and percentage points (10) are both accepted."""
    if is_missing(value):
        return "—"
    number = float(value)
    pct = number * 100 if abs(number) <= 1 else number
    digits = 0 if abs(pct - round(pct)) < 1e-9 else 2
    return f"{pct:.{digits}f}%".replace(".", ",")


def fmt_integer(value: Any) -> str:
    if is_missing(value):
        return "—"
    return f"{int(round(float(value))):,}".replace(",", " ")


def fmt_fraction(value: Any) -> str:
    if is_missing(value):
        return "—"
    return f"{float(value):.4f}".rstrip("0").rstrip(".").replace(".", ",")


def fmt_weekend_full_width(value: Any) -> tuple[str, str]:
    """Show the official one-sided shift and the derived full distance between limits."""
    if is_missing(value):
        return "—", "OffDaysTradingPriceRangeShift не получен"
    number = float(value)
    half_pct = number * 100 if abs(number) <= 1 else number
    full_pct = 2 * half_pct
    half_text = f"{half_pct:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    full_text = f"{full_pct:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    main = f"±{half_text}%"
    note = f"OffDaysTradingPriceRangeShift; полный диапазон {full_text}% от цены 23:50"
    return main, note


def weekend_range_details(value: Any) -> dict[str, str | float | None]:
    """Return the official shift and transparent derived relative-range values.

    The official NCC value is a fraction of the 23:50 reference price on one side.
    Values greater than 1 are interpreted as percentage points for compatibility
    with manually supplied CSV files.
    """
    if is_missing(value):
        return {
            "fraction": None,
            "parameter": "—",
            "side": "—",
            "full_width": "—",
            "lower_factor": "—",
            "upper_factor": "—",
            "factor_range": "—",
        }

    raw = float(value)
    fraction = raw / 100 if abs(raw) > 1 else raw
    side_pct = fraction * 100
    full_pct = 2 * side_pct

    def compact(number: float, max_digits: int = 4) -> str:
        return f"{number:.{max_digits}f}".rstrip("0").rstrip(".").replace(".", ",")

    lower_factor = 1 - fraction
    upper_factor = 1 + fraction
    return {
        "fraction": fraction,
        "parameter": compact(fraction, 6),
        "side": f"±{compact(side_pct, 2)}%",
        "full_width": f"{compact(full_pct, 2)}%",
        "lower_factor": compact(lower_factor, 6),
        "upper_factor": compact(upper_factor, 6),
        "factor_range": f"{compact(lower_factor, 6)} × P₍₂₃:₅₀₎ — {compact(upper_factor, 6)} × P₍₂₃:₅₀₎",
    }


def fmt_number(value: Any, decimals: int | None = None) -> str:
    """Format a quotation without hiding meaningful decimal places."""
    if is_missing(value):
        return "—"
    number = float(value)
    if decimals is None:
        magnitude = abs(number)
        if magnitude >= 1000:
            decimals = 0
        elif magnitude >= 10:
            decimals = 2
        elif magnitude >= 1:
            decimals = 3
        else:
            decimals = 5
    text = f"{number:,.{decimals}f}"
    text = text.replace(",", "\u00a0").replace(".", ",")
    if decimals > 0:
        text = text.rstrip("0").rstrip(",")
    return text


def fmt_rub(value: Any) -> str:
    if is_missing(value):
        return "—"
    number = float(value)
    decimals = 0 if abs(number) >= 100 else 2
    return f"{fmt_number(number, decimals)} ₽"


def fmt_compact_rub(value: Any) -> str:
    """Format large RUB amounts compactly while preserving the exact amount in notes."""
    if is_missing(value):
        return "—"
    number = float(value)
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        scaled = number / 1_000_000_000
        suffix = "млрд ₽"
    elif absolute >= 1_000_000:
        scaled = number / 1_000_000
        suffix = "млн ₽"
    elif absolute >= 1_000:
        scaled = number / 1_000
        suffix = "тыс. ₽"
    else:
        return fmt_rub(number)

    digits = 0 if abs(scaled - round(scaled)) < 1e-9 else 2
    return f"{scaled:.{digits}f} {suffix}".replace(".", ",")
