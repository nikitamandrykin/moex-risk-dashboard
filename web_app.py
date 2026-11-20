"""MOEX Risk Dashboard API without Streamlit.

The existing vetted MOEX/NCC parsers and mathematical functions are reused
unchanged. This is a read-only public application: no uploads or debug actions.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock
from time import time
from zoneinfo import ZoneInfo
import math
import os

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from services.boundaries import estimate_morning_limits, is_currency_future
from services.contracts import (
    concentration_limit_to_rub, contract_value_rub, contracts_for_asset,
    load_forts_contracts, morning_reference_price, price_to_rub,
    progressive_position_margin, reference_price,
)
from services.dashboard_ui import asset_labels_for_codes, available_overview_assets
from services.evening import load_evening_dataset
from services.formatters import fmt_compact_rub, fmt_integer, fmt_number, fmt_rate, fmt_rub, is_missing
from services.loaders import SourceStatus, latest_row, load_dataset, union_assetcodes
from services.offdays import load_offdays_dataset, supplement_offdays_with_published_values
from services.special_params import (
    active_special_parameters, calendar_assetcodes, calendar_wide_view,
    future_special_periods, load_special_calendar_dataset,
)

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
RUNTIME = BASE / "runtime_cache"
MSK = ZoneInfo("Europe/Moscow")
TTL_SECONDS = 300

MARKET_JSON = "https://iss.moex.com/iss/rms/engines/futures/objects/limits.json?iss.meta=off&iss.only=limits"
MARKET_CSV = "https://iss.moex.com/iss/rms/engines/futures/objects/limits.csv?iss.meta=off&iss.only=limits"
STATIC_JSON = "https://iss.moex.com/iss/rms/engines/futures/objects/staticparams.json?iss.meta=off&iss.only=staticparams"
STATIC_CSV = "https://iss.moex.com/iss/rms/engines/futures/objects/staticparams.csv?iss.meta=off&iss.only=staticparams"

def source_jobs():
    return {
        "market": lambda: load_dataset(
            name="MR/LK", env_url_name="NCC_MARKET_RATES_CSV_URL",
            cache_path=RUNTIME / "market_rates_last_good.csv",
            required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
            default_url=MARKET_JSON, alternate_urls=[MARKET_CSV],
        ),
        "static": lambda: load_dataset(
            name="Статические параметры", env_url_name="NCC_STATIC_PARAMS_CSV_URL",
            fallback_path=DATA / "static_params_fallback.csv",
            cache_path=RUNTIME / "static_params_last_good.csv",
            required_columns={"assetcode", "autoshiftnummr", "futmontime", "rangefut"},
            default_url=STATIC_JSON, alternate_urls=[STATIC_CSV],
        ),
        "evening": lambda: load_evening_dataset(
            fallback_path=DATA / "evening_static_params_2026-11-12.csv",
            cache_path=RUNTIME / "evening_params_last_good_2026_11.csv",
        ),
        "offdays": lambda: load_offdays_dataset(
            fallback_path=DATA / "offdays_params_fallback.csv",
            cache_path=RUNTIME / "offdays_last_good.xlsx",
        ),
        "contracts": lambda: load_forts_contracts(
            cache_path=RUNTIME / "forts_contracts_last_good.json",
        ),
        "special": lambda: load_special_calendar_dataset(
            fallback_path=DATA / "special_risk_calendar_2026.xlsx",
            cache_path=RUNTIME / "special_risk_calendar_last_good.xlsx",
        ),
    }


class DataStore:
    """Process-level TTL cache; never replace the good dataset with a raised error."""

    def __init__(self):
        self.lock = Lock()
        self.cached = None
        self.fetched_at = 0.0

    def get(self):
        with self.lock:
            if self.cached is not None and time() - self.fetched_at < TTL_SECONDS:
                return self.cached
            jobs = source_jobs()
            loaded = {}
            with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="ncc") as pool:
                tasks = {name: pool.submit(job) for name, job in jobs.items()}
                for name, task in tasks.items():
                    try:
                        loaded[name] = task.result()
                    except Exception as exc:
                        loaded[name] = (pd.DataFrame(), SourceStatus(name, "error", str(exc)))
            offdays, offdays_status = loaded["offdays"]
            loaded["offdays"] = (supplement_offdays_with_published_values(offdays), offdays_status)
            self.cached = loaded
            self.fetched_at = time()
            return loaded


store = DataStore()
app = FastAPI(title="MOEX Risk Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=str(BASE / "web_assets")), name="assets")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(BASE / "web_assets" / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    # Railway liveness must not depend on availability of external ISS/NCC.
    return {"status": "ok", "service": "MOEX Risk Dashboard", "framework": "FastAPI"}


def text_value(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (ValueError, TypeError):
        pass
    return str(value).strip()


def number(value):
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (ValueError, TypeError):
        return None


def numeric_limit(limit, lot_volume):
    a, b = number(limit), number(lot_volume)
    return a / b if a is not None and b is not None and b > 0 else None


def format_seconds(value):
    val = number(value)
    if val is None:
        return "—"
    seconds = round(val)
    return f"{seconds // 60} мин" if seconds % 60 == 0 else f"{seconds} сек"


def fmt_date(value):
    if value is None:
        return "—"
    try:
        parsed = pd.Timestamp(value)
        return parsed.strftime("%d.%m.%Y") if not pd.isna(parsed) else "—"
    except Exception:
        return "—"


def status_info(status):
    return {"name": status.name, "state": status.state, "updated_at": status.updated_at or ""}


def parameter(raw, code, label, fmt, note="", active=False):
    return {"code": code, "label": label, "value": fmt(raw), "note": note, "special": active}


def get_override(rows, parameter_name, default):
    if rows.empty or "parameter" not in rows.columns:
        return default, False
    matching = rows[rows["parameter"] == parameter_name]
    if matching.empty:
        return default, False
    return matching.iloc[-1].get("value"), True


def safe_asset_list(snapshot, *, include_calendar=False):
    codes = union_assetcodes(*(snapshot[k][0] for k in ["market", "static", "evening", "offdays", "contracts"]))
    if include_calendar:
        return sorted(set(codes).union(calendar_assetcodes(snapshot["special"][0])))
    return available_overview_assets(
        codes, snapshot["market"][0], snapshot["contracts"][0],
        asof=pd.Timestamp.now(tz=MSK).tz_localize(None),
    )


def asset_meta(snapshot):
    codes = safe_asset_list(snapshot)
    labels = asset_labels_for_codes(codes, snapshot["market"][0])
    return [{"code": code, "label": labels.get(code, code)} for code in codes]


@app.get("/api/bootstrap")
def bootstrap():
    snapshot = store.get()
    assets = asset_meta(snapshot)
    return {
        "assets": assets,
        "special_assets": safe_asset_list(snapshot, include_calendar=True),
        "sources": {key: status_info(status) for key, (_, status) in snapshot.items()},
        "fetched_at": datetime.fromtimestamp(store.fetched_at, tz=MSK).isoformat(),
        "ttl_seconds": TTL_SECONDS,
    }


def resolve_contract(snapshot, code, secid):
    rows = contracts_for_asset(snapshot["contracts"][0], code)
    if rows.empty or "secid" not in rows:
        return None, [], ""
    options = []
    for _, row in rows.iterrows():
        identifier = text_value(row.get("secid"))
        if identifier:
            options.append({
                "code": identifier,
                "label": (
                    f"{text_value(row.get('shortname')) or identifier} · {identifier} · "
                    f"последний торговый день {fmt_date(row.get('lasttradedate'))}"
                ),
            })
    if not options:
        return None, [], ""
    allowed = {opt["code"] for opt in options}
    selected = secid if secid in allowed else options[0]["code"]
    matched = rows[rows["secid"].astype(str).eq(selected)]
    contract_row = matched.iloc[0] if not matched.empty else None
    if contract_row is not None and text_value(contract_row.get("assetcode")).upper() != code:
        raise HTTPException(status_code=422, detail="БА выбранной серии не соответствует параметрам")
    return contract_row, options, selected


def special_for_asset(calendar, code, when):
    if calendar.empty:
        return {
            "state": "NO_DATA", "label": "НЕТ ДАННЫХ",
            "message": "Календарь специальных риск-параметров НКЦ недоступен.",
            "parameters": [], "future": [], "period": "—",
        }, calendar.iloc[0:0]
    active = active_special_parameters(calendar, code, when)
    listed = code in set(calendar_assetcodes(calendar))
    if not active.empty:
        start = active["start_at"].min()
        end = active["end_at"].max()
        state, label = "ACTIVE", "ДА"
        message = "На выбранный момент действует специальный режим НКЦ."
        period = f"{start:%d.%m %H:%M} — {end:%d.%m %H:%M}"
    elif listed:
        state, label = "INACTIVE", "НЕТ"
        message = f"Для {code} в выбранный момент специальный режим не действует."
        period = "—"
    else:
        state, label = "NOT_LISTED", "НЕ УКАЗАН"
        message = f"Для {code} в опубликованном календаре НКЦ специальные периоды не указаны."
        period = "—"
    params = []
    for _, row in active.iterrows():
        params.append({
            "code": text_value(row.get("parameter")),
            "label": text_value(row.get("parameter_title")),
            "value": text_value(row.get("value_raw")),
            "unit": text_value(row.get("unit")),
            "event": text_value(row.get("event_name")),
            "market_group": text_value(row.get("market_group")),
        })
    future = []
    future_rows = future_special_periods(calendar, code, when, limit=30)
    for _, row in future_rows.iterrows():
        future.append({
            "from": fmt_date(row.get("start_at")),
            "to": fmt_date(row.get("end_at")),
            "event": text_value(row.get("event_name")),
            "parameter": text_value(row.get("parameter")),
            "value": text_value(row.get("value_raw")),
        })
    return {
        "state": state, "label": label, "message": message,
        "parameters": params, "future": future, "period": period,
    }, active


def parse_check_at(value):
    if not value:
        return datetime.now(MSK)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="Неверный формат даты/времени")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MSK)
    return parsed.astimezone(MSK)


@app.get("/api/dashboard")
def dashboard(
    assetcode: str = Query(default="RTS", max_length=40),
    secid: str = Query(default="", max_length=50),
    quantity: int = Query(default=1, ge=0, le=10_000_000),
    check_at: str = Query(default="", max_length=40),
):
    snapshot = store.get()
    codes = safe_asset_list(snapshot, include_calendar=True)
    if not codes:
        return JSONResponse(status_code=503, content={"detail": "Источники MOEX/НКЦ не содержат данных"})
    code = assetcode.upper().strip()
    if code not in codes:
        raise HTTPException(status_code=404, detail="Базисный актив не найден")
    market = latest_row(snapshot["market"][0], code)
    static = latest_row(snapshot["static"][0], code)
    extra = latest_row(snapshot["evening"][0], code)
    offdays = latest_row(snapshot["offdays"][0], code)
    contract, options, selected = resolve_contract(snapshot, code, secid)
    now = parse_check_at(check_at)
    special, active = special_for_asset(snapshot["special"][0], code, now)

    def v(row, key):
        return row.get(key) if row is not None else None

    mr1, mr2, mr3 = (v(market, key) for key in ("mr1", "mr2", "mr3"))
    lk1, lk2 = (v(market, key) for key in ("lk1", "lk2"))
    lot_volume = v(contract, "lotvolume")
    step, step_price = v(contract, "minstep"), v(contract, "stepprice")
    initial_margin = v(contract, "initialmargin")
    price = reference_price(contract)
    value, value_note = contract_value_rub(contract, price)
    low, high = v(contract, "lowlimit"), v(contract, "highlimit")
    price_decimals = int(number(v(contract, "decimals")) or 0) if number(v(contract, "decimals")) is not None else None
    if price_decimals is not None:
        price_decimals = max(0, min(price_decimals, 12))
    lk1_count, lk2_count = numeric_limit(lk1, lot_volume), numeric_limit(lk2, lot_volume)
    last_rub = v(contract, "last_rub")
    lk1_rub = concentration_limit_to_rub(lk1, lot_volume, last_rub)
    lk2_rub = concentration_limit_to_rub(lk2, lot_volume, last_rub)

    weekend = v(offdays, "offdaystradingpricerangeshift")
    weekend_source = text_value(v(offdays, "offdays_source"))
    if is_missing(weekend):
        weekend = v(extra, "offdaystradingpricerangeshift")
    weekend_ref = morning_reference_price(contract)
    weekend_limits = estimate_morning_limits(
        current_low=low, current_high=high,
        reference_price=weekend_ref.value, offdays_shift=weekend,
    )
    total_shifts, total_special = get_override(active, "AutoShiftNumMR", v(static, "autoshiftnummr"))
    evening_shifts, evening_special = get_override(active, "AutoShiftNumMREvg", v(extra, "autoshiftnummrevg"))
    day_seconds, day_special = get_override(active, "FutMonTimeDay", v(static, "futmontime"))
    monitor_range, range_special = get_override(active, "RangeFut", v(static, "rangefut"))

    position = progressive_position_margin(
        quantity, lot_volume, lk1, lk2, mr1, mr2, mr3, initial_margin,
    )
    levels = []
    for key, rate in ((1, mr1), (2, mr2), (3, mr3)):
        levels.append({
            "code": f"MR{key}",
            "label": ("До LK1", "Сверх LK1 до LK2", "Сверх LK2")[key - 1],
            "contracts": fmt_number(getattr(position, f"level{key}_contracts"), 4),
            "margin": fmt_rub(getattr(position, f"level{key}_margin_rub")),
            "rate": fmt_rate(rate),
            "count": number(getattr(position, f"level{key}_contracts")) or 0,
        })
    missing = []
    if any(number(x) is None for x in (mr1, mr2, mr3, lk1, lk2)):
        missing.append("MR1–MR3 / LK1–LK2")
    for label, value in (
        ("AutoShiftNumMR", total_shifts),
        ("AutoShiftNumMREvg", evening_shifts),
        ("FutMonTimeDay", day_seconds),
        ("FutMonTimeEvg", v(extra, "futmontimeevg")),
        ("RangeFut", monitor_range),
        ("OffDaysTradingPriceRangeShift", weekend),
    ):
        if number(value) is None:
            missing.append(label)
    ratio = None
    if all(number(x) is not None for x in (low, high, price.value)) and float(high) > float(low):
        ratio = max(0.0, min(100.0, (float(price.value) - float(low)) / (float(high) - float(low)) * 100))
    down_pct = (
        (float(price.value) - float(low)) / abs(float(price.value)) * 100
        if number(price.value) not in (None, 0) and number(low) is not None else None
    )
    up_pct = (
        (float(high) - float(price.value)) / abs(float(price.value)) * 100
        if number(price.value) not in (None, 0) and number(high) is not None else None
    )
    asset_title = text_value(v(market, "title")) or text_value(v(offdays, "title"))
    currency = is_currency_future(code, asset_title)

    def field(label, value, code_="", note=""):
        return {"label": label, "value": value, "code": code_, "note": note}

    return {
        "assetcode": code,
        "label": asset_labels_for_codes([code], snapshot["market"][0]).get(code, code),
        "contract": {
            "selected": selected,
            "options": options,
            "name": text_value(v(contract, "shortname")) or selected,
            "tradedate": fmt_date(v(contract, "tradedate")),
            "lasttradedate": fmt_date(v(contract, "lasttradedate")),
        },
        "overview": [
            field("Цена ISS", fmt_number(price.value, price_decimals), note=f"{price.field.upper() or 'НЕТ ЦЕНЫ'} · дата торгов {fmt_date(v(contract, 'tradedate'))}"),
            field("Стоимость контракта", fmt_compact_rub(value), note=value_note),
            field("Нижняя граница", fmt_number(low, price_decimals), "LOWLIMIT"),
            field("Верхняя граница", fmt_number(high, price_decimals), "HIGHLIMIT"),
            field("Коридор выходных", fmt_rate(weekend), note=weekend_source),
        ],
        "concentration": [
            field("Базовый уровень", fmt_rate(mr1), "MR1"),
            field("Первый порог", f"{fmt_number(lk1_count, 0)} контр.", "LK1", f"≈ {fmt_compact_rub(lk1_rub)}"),
            field("Повышенный уровень", fmt_rate(mr2), "MR2"),
            field("Второй порог", f"{fmt_number(lk2_count, 0)} контр.", "LK2", f"≈ {fmt_compact_rub(lk2_rub)}"),
            field("Высокая концентрация", fmt_rate(mr3), "MR3"),
        ],
        "contract_parameters": [
            field("Размер лота", fmt_number(lot_volume, 4), "LOTVOLUME"),
            field("Минимальный шаг", fmt_number(step, 8), "MINSTEP"),
            field("Цена шага", fmt_rub(step_price), "STEPPRICE"),
            field("Один пункт", fmt_rub(number(step_price) / number(step)) if number(step) not in (None, 0) and number(step_price) is not None else "—", "RUB_PER_POINT"),
            field("ГО одного контракта · MR1", fmt_rub(initial_margin), "INITIALMARGIN"),
        ],
        "boundaries": {
            "low": fmt_number(low, price_decimals), "high": fmt_number(high, price_decimals),
            "price": fmt_number(price.value, price_decimals),
            "source": price.field.upper() or "НЕТ ЦЕНЫ",
            "tradedate": fmt_date(v(contract, "tradedate")),
            "ratio": ratio,
            "width": fmt_number(number(high) - number(low) if number(high) is not None and number(low) is not None else None, price_decimals),
            "to_low": fmt_number(number(price.value) - number(low) if number(price.value) is not None and number(low) is not None else None, price_decimals),
            "to_high": fmt_number(number(high) - number(price.value) if number(high) is not None and number(price.value) is not None else None, price_decimals),
            "to_low_pct": (f"{down_pct:.2f}%" if down_pct is not None else "—").replace(".", ","),
            "to_high_pct": (f"{up_pct:.2f}%" if up_pct is not None else "—").replace(".", ","),
            "shifts": [
                field("Общий лимит сдвигов", fmt_integer(total_shifts), "AutoShiftNumMR" + (" · SPECIAL" if total_special else "")),
                field("Мониторинг днём", format_seconds(day_seconds), "FutMonTimeDay" + (" · SPECIAL" if day_special else "")),
                field("Лимит вечером", fmt_integer(evening_shifts), "AutoShiftNumMREvg" + (" · SPECIAL" if evening_special else "")),
                field("Мониторинг вечером", format_seconds(v(extra, "futmontimeevg")), "FutMonTimeEvg"),
                field("Контрольная область", fmt_rate(monitor_range), "RangeFut" + (" · SPECIAL" if range_special else "")),
            ],
            "weekend": [
                field("Ширина ценового коридора в выходные", fmt_rate(weekend)),
                field("База расчёта · proxy", fmt_number(weekend_ref.value, price_decimals), note=weekend_ref.field),
                field("Нижняя граница · оценка", fmt_number(weekend_limits.effective_low, price_decimals)),
                field("Верхняя граница · оценка", fmt_number(weekend_limits.effective_high, price_decimals)),
            ],
            "weekend_error": weekend_limits.error,
            "morning_currency": currency,
            "morning": [
                field("Ширина коридора выходных", fmt_rate(weekend)),
                field("База расчёта · proxy", fmt_number(weekend_ref.value, price_decimals)),
                field("Нижняя граница · оценка", fmt_number(weekend_limits.effective_low, price_decimals)),
                field("Верхняя граница · оценка", fmt_number(weekend_limits.effective_high, price_decimals)),
            ] if currency else [],
        },
        "margin": {
            "quantity": quantity,
            "error": position.error,
            "total": fmt_rub(position.total_margin_rub),
            "position_ba": fmt_number(position.position_ba, 4),
            "highest": f"MR{position.highest_level}" if position.highest_level else "—",
            "lk1": lk1_count, "lk2": lk2_count,
            "levels": levels,
            "initial": fmt_rub(initial_margin),
            "note": "Аналитическая оценка простой позиции по одной серии, не официальный портфельный расчёт НКЦ.",
        },
        "special": special,
        "missing": missing,
        "source_warnings": [
            f"{name}: {status.state}"
            for name, (_, status) in snapshot.items() if status.state not in {"live"}
        ],
    }


@app.get("/api/special")
def special_lookup(
    assetcode: str = Query(max_length=40),
    check_at: str = Query(default="", max_length=40),
):
    snapshot = store.get()
    code = assetcode.upper().strip()
    if code not in safe_asset_list(snapshot, include_calendar=True):
        raise HTTPException(status_code=404, detail="Базисный актив не найден")
    result, _ = special_for_asset(snapshot["special"][0], code, parse_check_at(check_at))
    return result


@app.get("/api/methodology")
def methodology():
    frame = pd.read_csv(DATA / "methodology.csv", sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    return {"records": frame.to_dict("records")}


@app.get("/api/special/calendar")
def full_special_calendar():
    calendar = store.get()["special"][0]
    if calendar.empty:
        return {"rows": [], "message": "Нет данных календаря НКЦ"}
    rows = calendar_wide_view(calendar)
    # Dates and NaN are not JSON-native; don't expose raw internal XLSX rows.
    columns = [c for c in ("holiday_date", "assetcode", "asset_title", "event_name", "market_group", "start_at", "end_at") if c in rows.columns]
    columns += [c for c in ("AutoShiftNumMR", "FutMonTimeDay", "RangeFut", "AutoShiftNumMREvg", "FutMonTimeEvg") if c in rows.columns]
    records = []
    for _, row in rows[columns].iterrows():
        records.append({key: fmt_date(row[key]) if key == "holiday_date" else text_value(row[key]) for key in columns})
    return {"rows": records, "columns": columns}
