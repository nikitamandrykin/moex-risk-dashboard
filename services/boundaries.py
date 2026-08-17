from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
MORNING_START = time(6, 50)
MORNING_END = time(10, 0)




@dataclass(frozen=True)
class MorningLimitEstimate:
    reference_price: float | None
    shift_fraction: float | None
    offdays_low: float | None
    offdays_high: float | None
    effective_low: float | None
    effective_high: float | None
    down_distance: float | None
    up_distance: float | None
    auto_shift_count: int = 0
    error: str | None = None


def estimate_morning_limits(
    *,
    current_low: Any,
    current_high: Any,
    reference_price: Any,
    offdays_shift: Any,
) -> MorningLimitEstimate:
    """Estimate effective morning limits from the weekend corridor parameter.

    Methodology: H_morning=min(H_current,H_hol),
    L_morning=max(L_current,L_hol), where H_hol/L_hol are symmetric around
    Pmarket23:50 using OffDaysTradingPriceRangeShift. The public dashboard uses
    a labelled proxy for Pmarket23:50, so the result is an analytical estimate.
    Automatic shifts are not performed during the morning additional session.
    """
    def number(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return None if result != result else result

    reference = number(reference_price)
    shift = number(offdays_shift)
    low = number(current_low)
    high = number(current_high)
    if reference is None or shift is None:
        return MorningLimitEstimate(reference, shift, None, None, None, None, None, None, error="нет базы или OffDaysTradingPriceRangeShift")
    if abs(shift) > 1:
        shift /= 100
    if shift < 0:
        return MorningLimitEstimate(reference, shift, None, None, None, None, None, None, error="отрицательная ширина коридора")

    delta = abs(reference) * shift
    offdays_low = reference - delta
    offdays_high = reference + delta
    effective_low = max(value for value in (low, offdays_low) if value is not None)
    effective_high = min(value for value in (high, offdays_high) if value is not None)
    down_distance = reference - effective_low
    up_distance = effective_high - reference
    return MorningLimitEstimate(
        reference, shift, offdays_low, offdays_high, effective_low, effective_high,
        down_distance, up_distance, auto_shift_count=0, error=None,
    )


def is_currency_future(assetcode: str, title: str = "") -> bool:
    """Identify currency underlyings without maintaining a brittle full code list.

    Official NCC titles for currency underlyings contain the phrase ``на курс``.
    A small code fallback covers common perpetual aliases when a title is missing.
    """
    normalized_title = " ".join(str(title or "").lower().replace("ё", "е").split())
    if "на курс" in normalized_title or normalized_title.startswith("курс "):
        return True

    code = str(assetcode or "").strip().upper()
    explicit_codes = {
        "SI", "EU", "CNY", "AED", "AMD", "AUDU", "BYN", "ECAD", "ED",
        "EGBP", "EJPY", "EURM", "GBPU", "HKD", "INR", "KZT", "TRY",
        "UCAD", "UCHF", "UCNY", "UINR", "UJPY", "UKZT", "USDM", "UTRY",
        "CNYRUBTOM", "EURRUBTOM", "USDRUBTOM", "USDRUBF", "EURRUBF", "CNYRUBF",
    }
    return code in explicit_codes


def is_morning_session(now: datetime | None = None) -> bool:
    """Return True during the weekday FORTS morning period in Moscow time.

    The interval includes the opening auction from 06:50 and ends at 10:00.
    Exchange holidays are intentionally not inferred here; the live ISS source
    remains the authority on whether values are actually available.
    """
    current = now or datetime.now(MOSCOW_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW_TZ)
    else:
        current = current.astimezone(MOSCOW_TZ)
    return current.weekday() < 5 and MORNING_START <= current.time() < MORNING_END


def _load_store(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_morning_snapshot(path: Path, secid: str) -> dict[str, Any] | None:
    if not secid:
        return None
    item = _load_store(path).get(secid)
    return item if isinstance(item, dict) else None


def save_morning_snapshot(
    path: Path,
    *,
    secid: str,
    assetcode: str,
    low_quote: Any,
    high_quote: Any,
    low_rub: Any,
    high_rub: Any,
    source_time: str,
) -> dict[str, Any] | None:
    """Persist the latest observed morning LOWLIMIT/HIGHLIMIT for one series."""
    if not secid:
        return None

    def optional_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number:  # NaN
            return None
        return number

    low_number = optional_float(low_quote)
    high_number = optional_float(high_quote)
    if low_number is None or high_number is None:
        return None

    item = {
        "secid": secid,
        "assetcode": assetcode,
        "low_quote": low_number,
        "high_quote": high_number,
        "low_rub": optional_float(low_rub),
        "high_rub": optional_float(high_rub),
        "source_time": source_time,
        "captured_at": datetime.now(MOSCOW_TZ).isoformat(timespec="seconds"),
    }
    store = _load_store(path)
    store[secid] = item
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # The dashboard still displays live values even if its folder is read-only.
        pass
    return item
