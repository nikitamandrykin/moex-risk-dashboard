"""On-demand audit of official ISS data; never used for production pricing.

Exit 0: required assets match raw official ISS records and app parsers.
Exit 1: a mismatch in numeric values or identity was found.
Exit 2: BLOCKED/INCOMPLETE (source unreachable or instruments absent).
The audit explicitly checks RAW RESPONSE vs APP PARSER, not the Railway
session snapshot, which needs a separate same-time visual comparison.
"""
from __future__ import annotations

from datetime import datetime
from math import isclose, isfinite
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from services.contracts import (
    FORTS_CONTRACTS_URL,
    MARKETDATA_COLUMNS,
    SECURITIES_COLUMNS,
    contracts_for_asset,
    read_forts_payload,
    reference_price,
)
from services.loaders import read_moex_iss_json

TARGETS = ("AI92", "RTS", "Si", "CNY")
RMS_URL = "https://iss.moex.com/iss/rms/engines/futures/objects/limits.json"
PRICE_FIELDS = ("last", "settleprice", "lastsettleprice", "prevsettleprice")
RISK_FIELDS = ("mr1", "mr2", "mr3", "lk1", "lk2")
PRICE_DETAIL_FIELDS = ("last", "settleprice", "openposition", "last_rub")
CONTRACT_FIELDS = ("lowlimit", "highlimit", "initialmargin", "minstep", "stepprice", "lotvolume")


def block_rows(payload, name):
    block = payload.get(name, {})
    columns = [str(col).lower() for col in block.get("columns", [])]
    return [dict(zip(columns, row)) for row in block.get("data", [])]


def equal_number(left, right):
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError):
        return left is None and right is None
    return isfinite(a) and isfinite(b) and isclose(a, b, rel_tol=1e-10, abs_tol=1e-9)


def get_json(session, url, *, params):
    response = session.get(url, params=params, timeout=18)
    response.raise_for_status()
    return response.json(), response.url


def run():
    msk_now = datetime.now(ZoneInfo("Europe/Moscow"))
    print(f"Audit generated at {msk_now.isoformat()} (Moscow time)", flush=True)
    print("Purpose: validate official ISS raw response against the dashboard parser.")
    print("NOTE: API response availability ≠ current last trade; compare TRADEDATE and SYSTIME.")
    print("NOTE: this does NOT directly read the Railway dashboard snapshot.")
    with requests.Session() as session:
        session.headers.update({"User-Agent": "MOEX-Risk-Dashboard-validation/1.0", "Accept": "application/json"})
        try:
            risk_payload, risk_url = get_json(
                session, RMS_URL,
                params={"iss.meta": "off", "iss.only": "limits"},
            )
            futures_payload, futures_url = get_json(
                session, FORTS_CONTRACTS_URL,
                params={
                    "iss.meta": "off", "iss.only": "securities,marketdata",
                    "limit": "1000",
                    "securities.columns": ",".join(SECURITIES_COLUMNS),
                    "marketdata.columns": ",".join(MARKETDATA_COLUMNS),
                },
            )
        except (requests.RequestException, ValueError) as exc:
            print(f"BLOCKED: official ISS HTTP/JSON request unavailable: {type(exc).__name__}: {exc}", flush=True)
            return 2

    print(f"Source RMS: {risk_url}")
    print(f"Source FORTS: {futures_url}")
    limits_raw = block_rows(risk_payload, "limits")
    securities_raw = block_rows(futures_payload, "securities")
    marketdata_raw = block_rows(futures_payload, "marketdata")
    rates = read_moex_iss_json(
        __import__("json").dumps(risk_payload).encode("utf-8"), block_name="limits"
    )
    futures = read_forts_payload(futures_payload)
    print(f"Source counts: limits={len(limits_raw)}, futures={len(securities_raw)}, marketdata={len(marketdata_raw)}")
    if rates.empty or futures.empty:
        print("BLOCKED: at least one official endpoint returned no usable records")
        return 2

    problems, missing, checked = [], [], []
    for asset in TARGETS:
        key = asset.upper()
        risk_row = next((r for r in limits_raw if str(r.get("assetcode", "")).upper() == key), None)
        normalized = rates.loc[rates["assetcode"].astype(str).str.upper().eq(key)]
        if risk_row is None or normalized.empty:
            missing.append(f"{asset}: MR/LK not present in response")
            continue
        parsed_risk = normalized.iloc[-1]
        for fld in RISK_FIELDS:
            if fld not in parsed_risk or not equal_number(risk_row.get(fld), parsed_risk.get(fld)):
                problems.append(f"{asset}: {fld} RAW={risk_row.get(fld)} PARSED={parsed_risk.get(fld)}")
        active = contracts_for_asset(futures, asset, today=pd.Timestamp(msk_now.date()))
        if active.empty:
            missing.append(f"{asset}: no active future in ISS response")
            continue
        chosen = active.iloc[0]
        secid = str(chosen["secid"])
        if str(chosen.get("assetcode", "")).upper() != key:
            problems.append(f"{asset}: selected contract {secid} belongs to different assetcode")
        raw_security = next((r for r in securities_raw if str(r.get("secid", "")) == secid), None)
        raw_trade = next((r for r in marketdata_raw if str(r.get("secid", "")) == secid), None)
        if raw_security is None:
            problems.append(f"{asset}: {secid} absent from RAW securities")
            continue
        for fld in CONTRACT_FIELDS:
            if fld in raw_security and not equal_number(raw_security.get(fld), chosen.get(fld)):
                problems.append(f"{asset}/{secid}: {fld} RAW={raw_security.get(fld)} PARSED={chosen.get(fld)}")
        if raw_trade is not None:
            for fld in PRICE_DETAIL_FIELDS:
                if fld in raw_trade and not equal_number(raw_trade.get(fld), chosen.get(fld)):
                    problems.append(f"{asset}/{secid}: {fld} RAW={raw_trade.get(fld)} PARSED={chosen.get(fld)}")
        chosen_price = reference_price(chosen)
        if chosen_price.field in PRICE_FIELDS:
            source = (raw_trade or {}) if chosen_price.field in ("last", "settleprice") else raw_security
            if not equal_number(source.get(chosen_price.field), chosen_price.value):
                problems.append(f"{asset}/{secid}: source price {chosen_price.field} differs from RAW")
        checked.append(asset)
        metrics = " ".join(f"{name.upper()}={parsed_risk.get(name)}" for name in RISK_FIELDS)
        print(
            f"CHECK {asset} {secid} {metrics} "
            f"PRICE={chosen_price.value} FIELD={chosen_price.field} "
            f"LOW={chosen.get('lowlimit')} HIGH={chosen.get('highlimit')} "
            f"IM={chosen.get('initialmargin')} "
            f"TRADEDATE={chosen.get('tradedate', '—')} SYSTIME={chosen.get('systime', '—')} "
            f"LASTTRADEDATE={chosen.get('lasttradedate', '—')}", flush=True
        )

    for item in problems:
        print(f"MISMATCH: {item}")
    for item in missing:
        print(f"INCOMPLETE: {item}")
    if problems:
        print(f"FAIL: {len(problems)} raw-vs-parser mismatches detected")
        return 1
    if missing:
        print(f"INCOMPLETE: only {len(checked)}/{len(TARGETS)} instruments available; do not treat this as a passed live audit.")
        return 2
    print(f"PASS: {len(checked)} assets checked, no raw-vs-parser mismatch. Railway snapshot still requires same-time visual comparison.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
