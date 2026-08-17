from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import tempfile
import time

import pandas as pd
import requests

from services.loaders import SourceStatus, dataframe_timestamp, path_timestamp


FORTS_CONTRACTS_URL = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"

SECURITIES_COLUMNS = [
    "SECID",
    "SHORTNAME",
    "SECNAME",
    "ASSETCODE",
    "PREVSETTLEPRICE",
    "DECIMALS",
    "MINSTEP",
    "LASTTRADEDATE",
    "LASTDELDATE",
    "LOTVOLUME",
    "INITIALMARGIN",
    "HIGHLIMIT",
    "LOWLIMIT",
    "STEPPRICE",
    "LASTSETTLEPRICE",
    "IMTIME",
    "SETTLEPRICE_CLR",
]

MARKETDATA_COLUMNS = [
    "SECID",
    "LAST",
    "SETTLEPRICE",
    "OPENPOSITION",
    "UPDATETIME",
    "SYSTIME",
    "TRADEDATE",
    "TRADE_SESSION_DATE",
    "LAST_RUB",
]

NUMERIC_COLUMNS = {
    "prevsettleprice",
    "decimals",
    "minstep",
    "lotvolume",
    "initialmargin",
    "highlimit",
    "lowlimit",
    "stepprice",
    "lastsettleprice",
    "settleprice_clr",
    "last",
    "settleprice",
    "openposition",
    "last_rub",
}

DATE_COLUMNS = {"lasttradedate", "lastdeldate", "tradedate", "trade_session_date"}
DATETIME_COLUMNS = {"imtime", "systime"}


@dataclass(frozen=True)
class PriceReference:
    value: float | None
    field: str
    label: str
    is_current: bool


def _block_to_frame(payload: dict[str, Any], block_name: str) -> pd.DataFrame:
    block = payload.get(block_name) or {}
    columns = [str(column).strip().lower() for column in block.get("columns", [])]
    data = block.get("data", [])
    if not columns:
        return pd.DataFrame()
    frame = pd.DataFrame(data, columns=columns)
    return frame


def read_forts_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert the official MOEX ISS FORTS response into one contract table."""
    securities = _block_to_frame(payload, "securities")
    marketdata = _block_to_frame(payload, "marketdata")
    if securities.empty:
        return pd.DataFrame()

    if not marketdata.empty and "secid" in marketdata.columns:
        frame = securities.merge(marketdata, on="secid", how="left", suffixes=("", "_market"))
    else:
        frame = securities.copy()

    for column in NUMERIC_COLUMNS.intersection(frame.columns):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    for column in DATE_COLUMNS.intersection(frame.columns):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")

    for column in DATETIME_COLUMNS.intersection(frame.columns):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")

    for column in ("assetcode", "secid", "shortname", "secname"):
        if column in frame.columns:
            frame[column] = frame[column].astype("string").str.strip()

    if "assetcode" in frame.columns:
        frame = frame[frame["assetcode"].notna() & frame["assetcode"].ne("")]

    return frame.reset_index(drop=True)


def _validate_contract_frame(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise ValueError("ISS вернул пустой список фьючерсных контрактов")
    missing = {"secid", "assetcode"}.difference(frame.columns)
    if missing:
        raise ValueError("ISS не вернул обязательные поля: " + ", ".join(sorted(missing)))
    if not frame["secid"].notna().any() or not frame["assetcode"].notna().any():
        raise ValueError("ISS вернул контрактные строки без SECID/ASSETCODE")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    Path(temp_name).replace(path)


def load_forts_contracts(
    timeout: int = 25,
    *,
    cache_path: Path | None = None,
) -> tuple[pd.DataFrame, SourceStatus]:
    """Load current FORTS data with a last-known-good JSON snapshot."""
    params = {
        "iss.meta": "off",
        "iss.only": "securities,marketdata",
        "limit": "1000",
        "securities.columns": ",".join(SECURITIES_COLUMNS),
        "marketdata.columns": ",".join(MARKETDATA_COLUMNS),
    }
    live_error = ""
    response = None
    for attempt in range(3):
        try:
            response = requests.get(
                FORTS_CONTRACTS_URL,
                params=params,
                timeout=timeout,
                headers={
                    "User-Agent": "MOEX-Risk-Dashboard/0.9 (+internal analytical MVP)",
                    "Accept": "application/json,*/*",
                },
            )
            response.raise_for_status()
            payload = response.json()
            frame = read_forts_payload(payload)
            _validate_contract_frame(frame)
            if cache_path is not None:
                try:
                    _atomic_write(
                        cache_path,
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                    )
                except OSError:
                    pass
            return frame, SourceStatus(
                "Контракты ISS", "live", response.url, dataframe_timestamp(frame)
            )
        except Exception as exc:
            live_error = str(exc)
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))

    if cache_path is not None and cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            frame = read_forts_payload(payload)
            _validate_contract_frame(frame)
            return frame, SourceStatus(
                "Контракты ISS",
                "cache",
                f"последний успешный снимок: {cache_path.name}; live-источник недоступен: {live_error}",
                dataframe_timestamp(frame) or path_timestamp(cache_path),
            )
        except Exception as cache_exc:
            return pd.DataFrame(), SourceStatus(
                "Контракты ISS",
                "error",
                f"live: {live_error}; cache: {cache_exc}",
            )

    return pd.DataFrame(), SourceStatus("Контракты ISS", "error", live_error)


def contracts_for_asset(
    frame: pd.DataFrame,
    assetcode: str,
    *,
    today: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return active contracts for an asset, ordered by useful default priority."""
    if frame.empty or "assetcode" not in frame.columns:
        return pd.DataFrame()

    rows = frame[frame["assetcode"].astype(str).str.upper() == assetcode.upper()].copy()
    if rows.empty:
        return rows

    reference_date = (today or pd.Timestamp.today()).normalize()
    if "lasttradedate" in rows.columns:
        active = rows[rows["lasttradedate"].isna() | (rows["lasttradedate"] >= reference_date)]
        if not active.empty:
            rows = active

    price_fields = [field for field in ("last", "settleprice", "lastsettleprice", "prevsettleprice") if field in rows.columns]
    if price_fields:
        rows["_has_price"] = rows[price_fields].notna().any(axis=1).astype(int)
    else:
        rows["_has_price"] = 0

    if "openposition" not in rows.columns:
        rows["openposition"] = 0
    rows["openposition"] = pd.to_numeric(rows["openposition"], errors="coerce").fillna(0)

    sort_columns: list[str] = ["_has_price"]
    ascending: list[bool] = [False]
    if "lasttradedate" in rows.columns:
        sort_columns.append("lasttradedate")
        ascending.append(True)
    sort_columns.append("openposition")
    ascending.append(False)

    rows = rows.sort_values(sort_columns, ascending=ascending, na_position="last")
    return rows.drop(columns=["_has_price"], errors="ignore").reset_index(drop=True)


def reference_price(row: pd.Series | None) -> PriceReference:
    """Choose the best available price while keeping its exact source transparent."""
    if row is None:
        return PriceReference(None, "", "Цена не получена", False)

    candidates = (
        ("last", "LAST — последняя сделка", True),
        ("settleprice", "SETTLEPRICE — текущая расчётная цена", True),
        ("lastsettleprice", "LASTSETTLEPRICE — цена последнего клиринга", False),
        ("prevsettleprice", "PREVSETTLEPRICE — расчётная цена предыдущего дня", False),
    )
    for field, label, is_current in candidates:
        value = row.get(field)
        if value is not None and not pd.isna(value):
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number != 0:
                return PriceReference(number, field, label, is_current)

    return PriceReference(None, "", "Цена не получена", False)


def morning_reference_price(row: pd.Series | None) -> PriceReference:
    """Choose a transparent proxy for the methodology's Pmarket23:50.

    Pmarket23:50 is a separately calculated methodology value and is not
    exposed as a dedicated public ISS field. PREVSETTLEPRICE is therefore
    preferred as the most stable previous-session proxy; later fields are
    fallbacks only.
    """
    if row is None:
        return PriceReference(None, "", "База для утренней оценки не получена", False)
    candidates = (
        ("prevsettleprice", "PREVSETTLEPRICE — прокси Pmarket23:50", False),
        ("lastsettleprice", "LASTSETTLEPRICE — прокси Pmarket23:50", False),
        ("settleprice", "SETTLEPRICE — резервная база утренней оценки", True),
        ("last", "LAST — резервная база утренней оценки", True),
    )
    for field, label, is_current in candidates:
        value = row.get(field)
        if value is None or pd.isna(value):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != 0:
            return PriceReference(number, field, label, is_current)
    return PriceReference(None, "", "База для утренней оценки не получена", False)


def price_to_rub(price: Any, minstep: Any, stepprice: Any) -> float | None:
    """Convert a futures quotation into its ruble equivalent using tick economics."""
    try:
        price_number = float(price)
        minstep_number = float(minstep)
        stepprice_number = float(stepprice)
    except (TypeError, ValueError):
        return None
    if pd.isna(price_number) or pd.isna(minstep_number) or pd.isna(stepprice_number):
        return None
    if minstep_number == 0:
        return None
    return price_number / minstep_number * stepprice_number


def contract_value_rub(row: pd.Series | None, price: PriceReference) -> tuple[float | None, str]:
    """Return current contract value and explain whether ISS supplied it directly."""
    if row is None or price.value is None:
        return None, "стоимость не рассчитана"

    last_rub = row.get("last_rub")
    if price.field == "last" and last_rub is not None and not pd.isna(last_rub):
        try:
            return float(last_rub), "LAST_RUB — официальное поле ISS"
        except (TypeError, ValueError):
            pass

    value = price_to_rub(price.value, row.get("minstep"), row.get("stepprice"))
    return value, "расчёт: цена ÷ MINSTEP × STEPPRICE"



def concentration_limit_to_rub(
    lk: Any,
    lot_volume: Any,
    last_rub: Any,
) -> float | None:
    """Return the nominal RUB equivalent of an LK threshold.

    Formula: ``LK / LOTVOLUME * LAST_RUB``. The intermediate contract
    equivalent is not exposed because the dashboard only needs LK1_RUB and
    LK2_RUB.
    """
    try:
        lk_number = float(lk)
        lot_number = float(lot_volume)
        last_rub_number = float(last_rub)
    except (TypeError, ValueError):
        return None
    if (
        pd.isna(lk_number)
        or pd.isna(lot_number)
        or pd.isna(last_rub_number)
        or lot_number <= 0
    ):
        return None
    return lk_number / lot_number * last_rub_number



@dataclass(frozen=True)
class PositionMarginBreakdown:
    total_margin_rub: float | None
    position_ba: float | None
    lk1_contracts: float | None
    lk2_contracts: float | None
    level1_contracts: float | None
    level2_contracts: float | None
    level3_contracts: float | None
    level1_margin_rub: float | None
    level2_margin_rub: float | None
    level3_margin_rub: float | None
    level2_multiplier: float | None
    level3_multiplier: float | None
    highest_level: int | None
    error: str | None = None


def progressive_position_margin(
    contracts_count: Any,
    lot_volume: Any,
    lk1: Any,
    lk2: Any,
    mr1: Any,
    mr2: Any,
    mr3: Any,
    initial_margin: Any,
) -> PositionMarginBreakdown:
    """Estimate one-series futures margin across concentration levels.

    The official ``INITIALMARGIN`` of the selected contract is used as the
    first-level amount per contract. Marginal parts of the position above LK1
    and LK2 are scaled by ``MR2 / MR1`` and ``MR3 / MR1`` respectively.

    LK thresholds are expressed in underlying-asset units, so the position is
    split in BA units first. This also handles a whole contract that crosses a
    threshold by allocating a fractional contract equivalent to each level.
    The function is an analytical estimate for a simple position in one series;
    it is not a replacement for NCC portfolio margining.
    """

    def error_result(
        message: str,
        *,
        position_ba: float | None = None,
        lk1_contracts: float | None = None,
        lk2_contracts: float | None = None,
        level1_contracts: float | None = None,
        level2_contracts: float | None = None,
        level2_multiplier: float | None = None,
    ) -> PositionMarginBreakdown:
        return PositionMarginBreakdown(
            total_margin_rub=None,
            position_ba=position_ba,
            lk1_contracts=lk1_contracts,
            lk2_contracts=lk2_contracts,
            level1_contracts=level1_contracts,
            level2_contracts=level2_contracts,
            level3_contracts=None,
            level1_margin_rub=None,
            level2_margin_rub=None,
            level3_margin_rub=None,
            level2_multiplier=level2_multiplier,
            level3_multiplier=None,
            highest_level=None,
            error=message,
        )

    def optional_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(number) else number

    try:
        contracts_number = abs(float(contracts_count))
        lot_number = float(lot_volume)
        lk1_number = float(lk1)
        initial_margin_number = float(initial_margin)
        mr1_number = float(mr1)
    except (TypeError, ValueError):
        return error_result("Нет обязательных данных первого уровня")

    if (
        pd.isna(contracts_number)
        or pd.isna(lot_number)
        or pd.isna(lk1_number)
        or pd.isna(initial_margin_number)
        or pd.isna(mr1_number)
        or lot_number <= 0
        or lk1_number < 0
        or initial_margin_number < 0
        or mr1_number <= 0
    ):
        return error_result("Некорректные данные первого уровня")

    position_ba = contracts_number * lot_number
    lk1_contracts = lk1_number / lot_number
    lk2_number = optional_number(lk2)
    mr2_number = optional_number(mr2)
    mr3_number = optional_number(mr3)
    lk2_contracts = (lk2_number / lot_number) if lk2_number is not None and lk2_number >= lk1_number else None
    level2_multiplier = (mr2_number / mr1_number) if mr2_number is not None and mr2_number >= 0 else None
    level3_multiplier = (mr3_number / mr1_number) if mr3_number is not None and mr3_number >= 0 else None

    level1_ba = min(position_ba, lk1_number)
    level2_ba = 0.0
    level3_ba = 0.0

    if position_ba > lk1_number:
        if lk2_number is None or lk2_number < lk1_number or mr2_number is None or mr2_number < 0:
            return error_result(
                "Для части сверх LK1 не получены корректные LK2 и MR2",
                position_ba=position_ba,
                lk1_contracts=lk1_contracts,
                lk2_contracts=lk2_contracts,
                level1_contracts=level1_ba / lot_number,
                level2_multiplier=level2_multiplier,
            )
        level2_ba = min(position_ba - lk1_number, lk2_number - lk1_number)
        level3_ba = max(position_ba - lk2_number, 0.0)

        if level3_ba > 0 and (mr3_number is None or mr3_number < 0):
            return error_result(
                "Для части сверх LK2 не получен корректный MR3",
                position_ba=position_ba,
                lk1_contracts=lk1_contracts,
                lk2_contracts=lk2_contracts,
                level1_contracts=level1_ba / lot_number,
                level2_contracts=level2_ba / lot_number,
                level2_multiplier=level2_multiplier,
            )

    base_margin_per_ba = initial_margin_number / lot_number
    level1_margin = level1_ba * base_margin_per_ba
    level2_margin = (
        level2_ba * base_margin_per_ba * level2_multiplier
        if level2_ba > 0 and level2_multiplier is not None
        else 0.0
    )
    level3_margin = (
        level3_ba * base_margin_per_ba * level3_multiplier
        if level3_ba > 0 and level3_multiplier is not None
        else 0.0
    )

    if contracts_number == 0:
        highest_level = 0
    elif level3_ba > 0:
        highest_level = 3
    elif level2_ba > 0:
        highest_level = 2
    else:
        highest_level = 1

    return PositionMarginBreakdown(
        total_margin_rub=level1_margin + level2_margin + level3_margin,
        position_ba=position_ba,
        lk1_contracts=lk1_contracts,
        lk2_contracts=lk2_contracts,
        level1_contracts=level1_ba / lot_number,
        level2_contracts=level2_ba / lot_number,
        level3_contracts=level3_ba / lot_number,
        level1_margin_rub=level1_margin,
        level2_margin_rub=level2_margin,
        level3_margin_rub=level3_margin,
        level2_multiplier=level2_multiplier,
        level3_multiplier=level3_multiplier,
        highest_level=highest_level,
        error=None,
    )

def simple_position_margin_before_lk1(
    contracts_count: Any,
    lot_volume: Any,
    lk1: Any,
    initial_margin: Any,
) -> tuple[float | None, float | None, bool | None]:
    """Calculate margin for a simple one-series futures position before LK1.

    The calculation intentionally covers only the first concentration level:

    ``position_ba = abs(contracts_count) * LOTVOLUME``
    ``margin_rub = abs(contracts_count) * INITIALMARGIN``

    If the position exceeds ``LK1`` or any required input is unavailable, the
    function does not extrapolate the margin and returns ``None`` for it.
    """
    try:
        contracts_number = abs(float(contracts_count))
        lot_number = float(lot_volume)
        lk1_number = float(lk1)
        initial_margin_number = float(initial_margin)
    except (TypeError, ValueError):
        return None, None, None

    if (
        pd.isna(contracts_number)
        or pd.isna(lot_number)
        or pd.isna(lk1_number)
        or pd.isna(initial_margin_number)
        or lot_number <= 0
        or lk1_number < 0
        or initial_margin_number < 0
    ):
        return None, None, None

    position_ba = contracts_number * lot_number
    within_lk1 = position_ba <= lk1_number
    margin_rub = contracts_number * initial_margin_number if within_lk1 else None
    return margin_rub, position_ba, within_lk1


def weekend_limits_from_price(price: Any, shift_fraction: Any) -> tuple[float | None, float | None]:
    """Apply the methodology's symmetric shift to any supplied reference price."""
    try:
        price_number = float(price)
        shift_number = float(shift_fraction)
    except (TypeError, ValueError):
        return None, None
    if pd.isna(price_number) or pd.isna(shift_number):
        return None, None
    if abs(shift_number) > 1:
        shift_number /= 100
    delta = abs(price_number) * shift_number
    return price_number - delta, price_number + delta
