from __future__ import annotations

from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from .contracts import contracts_for_asset, reference_price
from .boundaries import is_currency_future
from .loaders import latest_row
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
    if any(token in text for token in ("индекс", "index", "imoex", "ртс")):
        return "Индексы"
    if any(token in text for token in ("золот", "серебр", "паллад", "платин", "metal", "gold", "silver")):
        return "Металлы"
    if any(token in text for token in (
        "нефт", "газ", "brent", "crude", "пшениц", "кукуруз", "сахар", "кофе",
        "какао", "хлоп", "алюмин", "медь", "никел", "цинк", "товар",
    )):
        return "Товары"
    if any(token in text for token in ("акци", "обыкновенн", "привилегированн", "share", "stock")):
        return "Акции"
    return "Прочие"


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
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
    codes = sorted({str(x).strip() for x in contracts_df["assetcode"].dropna() if str(x).strip()})
    rows: list[dict[str, object]] = []

    for code in codes:
        candidates = contracts_for_asset(contracts_df, code)
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
            if low_distance <= high_distance:
                nearest_side, nearest_pct = "LOW", low_distance
            else:
                nearest_side, nearest_pct = "HIGH", high_distance
        elif low_distance is not None:
            nearest_side, nearest_pct = "LOW", low_distance
        elif high_distance is not None:
            nearest_side, nearest_pct = "HIGH", high_distance

        if price is not None and low is not None and price < low:
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

        market_row = latest_row(market_df, code)
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
    preferred = ["Валюта", "Индексы", "Акции", "Товары", "Металлы", "Прочие"]
    available = {str(x) for x in frame["group"].dropna()}
    return [item for item in preferred if item in available]
