from __future__ import annotations

from datetime import datetime
import math
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from .contracts import contracts_for_asset, reference_price
from .boundaries import is_currency_future
from .loaders import latest_row, _assetcode_key
from .special_params import active_special_parameters

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def classify_asset_group(assetcode: str, title: str = "", contract_name: str = "") -> str:
    """Analytical grouping for monitor filters.

    This is intentionally a UI convenience, not an official MOEX/NCC
    classification. The title/contract text is preferred to brittle ticker lists.
    """
    code = str(assetcode or "").strip().upper()
    text = " ".join(str(x or "") for x in (title, contract_name)).lower().replace("ё", "е")

    if is_currency_future(code, title):
        return "Валюта"

    # Money-market / interest-rate futures deserve their own bucket.  Without
    # it RUON/RUONIA/1MFR end up in "Прочие" (or even "Индексы" when the
    # instrument description mentions an index used to calculate the rate).
    rate_codes = {"RUON", "RUONIA", "1MFR", "RUSFAR"}
    if code in rate_codes or any(
        token in text for token in ("ставк", "interest rate", "rate future", "ruonia", "rusfar", "mosprime")
    ):
        return "Ставки"

    index_codes = {"RTS", "IMOEX", "MIX", "MXI", "RVI", "HANG"}
    if code in index_codes or any(token in text for token in ("индекс", "index", "imoex", "ртс")):
        return "Индексы"
    if any(token in text for token in ("золот", "серебр", "паллад", "платин", "metal", "gold", "silver")):
        return "Металлы"
    if any(token in text for token in (
        "нефт", "газ", "brent", "crude", "бензин", "gasoline", "ai-92", "ai92",
        "пшениц", "кукуруз", "сахар", "кофе", "какао", "хлоп", "алюмин", "медь",
        "никел", "цинк", "товар",
    )):
        return "Товары"
    if any(token in text for token in ("акци", "обыкновенн", "привилегированн", "share", "stock", "etf")):
        return "Акции"
    return "Прочие"


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _distance_pct(price: float | None, boundary: float | None) -> float | None:
    if price is None or boundary is None:
        return None
    denominator = abs(price)
    if denominator <= 1e-12:
        return None
    return abs(boundary - price) / denominator * 100.0


def build_market_monitor(
    contracts_df: pd.DataFrame,
    market_df: pd.DataFrame,
    special_calendar_df: pd.DataFrame,
    *,
    check_at: datetime | None = None,
    attention_threshold_pct: float = 2.0,
    critical_threshold_pct: float = 0.75,
) -> pd.DataFrame:
    """Build one representative active contract per underlying for risk monitoring.

    ``attention_threshold_pct`` and ``critical_threshold_pct`` are analytical UI
    thresholds and are deliberately kept separate from official NCC RangeFut.
    """
    if contracts_df is None or contracts_df.empty or "assetcode" not in contracts_df.columns:
        return pd.DataFrame()

    check_at = check_at or datetime.now(MOSCOW_TZ)
    asof_date = pd.Timestamp(check_at)
    if asof_date.tzinfo is not None:
        asof_date = asof_date.tz_convert(MOSCOW_TZ).tz_localize(None)
    asof_date = asof_date.normalize()
    codes = sorted({str(x).strip() for x in contracts_df["assetcode"].dropna() if str(x).strip()})

    # Group once instead of scanning the full contract and risk-rate tables for
    # every underlying. Keep contracts_for_asset/latest_row for exact priority.
    contract_keys = contracts_df["assetcode"].astype(str).str.upper()
    contracts_by_code = {
        str(key): group
        for key, group in contracts_df.groupby(contract_keys, sort=False)
    }
    market_by_code = {}
    if market_df is not None and not market_df.empty and "assetcode" in market_df.columns:
        market_keys = market_df["assetcode"].map(_assetcode_key)
        market_by_code = {
            str(key): group
            for key, group in market_df.groupby(market_keys, sort=False)
        }
    rows: list[dict[str, object]] = []

    for code in codes:
        code_contracts = contracts_by_code.get(code.upper())
        if code_contracts is None:
            continue
        candidates = contracts_for_asset(code_contracts, code, today=asof_date)
        if "lasttradedate" in candidates.columns:
            # contracts_for_asset falls back to expired series if no active ones
            # exist; Risk Radar must never present them as active.
            trading_end = pd.to_datetime(candidates["lasttradedate"], errors="coerce")
            candidates = candidates[trading_end.isna() | (trading_end >= asof_date)]
        if candidates.empty:
            continue

        chosen: pd.Series | None = None
        chosen_ref = None
        # Prefer the nearest active contract that has price and both official limits.
        for _, candidate in candidates.iterrows():
            ref = reference_price(candidate)
            low = _number(candidate.get("lowlimit"))
            high = _number(candidate.get("highlimit"))
            if ref.value is not None and low is not None and high is not None:
                chosen = candidate
                chosen_ref = ref
                break
        if chosen is None:
            chosen = candidates.iloc[0]
            chosen_ref = reference_price(chosen)

        price = _number(chosen_ref.value if chosen_ref else None)
        low = _number(chosen.get("lowlimit"))
        high = _number(chosen.get("highlimit"))
        low_distance = _distance_pct(price, low)
        high_distance = _distance_pct(price, high)

        nearest_side = "—"
        nearest_pct: float | None = None
        if low_distance is not None and high_distance is not None:
            # If both distances are visually identical at the precision shown in
            # the monitor, do not invent a direction. Keep the smaller distance
            # for alerting, but label the position as CENTER / equidistant.
            if round(low_distance, 2) == round(high_distance, 2):
                nearest_side, nearest_pct = "CENTER", min(low_distance, high_distance)
            elif low_distance < high_distance:
                nearest_side, nearest_pct = "LOW", low_distance
            else:
                nearest_side, nearest_pct = "HIGH", high_distance
        elif low_distance is not None:
            nearest_side, nearest_pct = "LOW", low_distance
        elif high_distance is not None:
            nearest_side, nearest_pct = "HIGH", high_distance

        if price is None or low is None or high is None or high <= low:
            risk_status = "NO DATA"
        elif price < low:
            risk_status = "OUTSIDE LOW"
        elif price is not None and high is not None and price > high:
            risk_status = "OUTSIDE HIGH"
        elif nearest_pct is not None and nearest_pct <= critical_threshold_pct:
            risk_status = "CRITICAL"
        elif nearest_pct is not None and nearest_pct <= attention_threshold_pct:
            risk_status = "WATCH"
        else:
            risk_status = "NORMAL"

        position_pct = None
        if price is not None and low is not None and high is not None and high > low:
            position_pct = max(0.0, min(100.0, (price - low) / (high - low) * 100.0))

        matching_market = market_by_code.get(_assetcode_key(code))
        market_row = latest_row(matching_market, code) if matching_market is not None else None
        title = ""
        if market_row is not None:
            value = market_row.get("title")
            if value is not None and not pd.isna(value):
                title = str(value).strip()
        contract_name = str(chosen.get("shortname") or chosen.get("secname") or "").strip()
        special_rows = active_special_parameters(special_calendar_df, code, check_at)
        special_mode = not special_rows.empty
        special_group = ""
        if special_mode:
            special_group = str(special_rows.iloc[-1].get("market_group") or "").strip()

        rows.append(
            {
                "assetcode": code,
                "title": title,
                "group": classify_asset_group(code, title, contract_name),
                "secid": str(chosen.get("secid") or "").strip(),
                "contract": contract_name or str(chosen.get("secid") or "").strip(),
                "price": price,
                "lowlimit": low,
                "highlimit": high,
                "distance_low_pct": low_distance,
                "distance_high_pct": high_distance,
                "nearest_side": nearest_side,
                "nearest_pct": nearest_pct,
                "position_pct": position_pct,
                "risk_status": risk_status,
                "special_mode": special_mode,
                "special_group": special_group,
                "price_source": chosen_ref.field if chosen_ref else "",
                "systime": chosen.get("systime"),
            }
        )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["_sort_distance"] = pd.to_numeric(frame["nearest_pct"], errors="coerce").fillna(float("inf"))
    status_rank = {"OUTSIDE LOW": 0, "OUTSIDE HIGH": 0, "CRITICAL": 1, "WATCH": 2, "NORMAL": 3}
    frame["_status_rank"] = frame["risk_status"].map(status_rank).fillna(9)
    frame = frame.sort_values(["_status_rank", "_sort_distance", "assetcode"], kind="stable")
    return frame.drop(columns=["_sort_distance", "_status_rank"]).reset_index(drop=True)


def monitor_groups(frame: pd.DataFrame) -> list[str]:
    if frame is None or frame.empty or "group" not in frame.columns:
        return []
    preferred = ["Валюта", "Ставки", "Индексы", "Акции", "Товары", "Металлы", "Прочие"]
    available = {str(x) for x in frame["group"].dropna()}
    return [item for item in preferred if item in available]

