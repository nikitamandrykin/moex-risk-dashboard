from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable
import gzip
import os

import pandas as pd
import requests

from .loaders import SourceStatus


DEFAULT_HISTORY_URL = (
    "https://raw.githubusercontent.com/nikitamandrykin/moex-risk-dashboard/"
    "risk-history/risk_radar_history.csv.gz"
)
HISTORY_COLUMNS = [
    "captured_at",
    "assetcode",
    "secid",
    "price",
    "lowlimit",
    "highlimit",
    "distance_low_pct",
    "distance_high_pct",
    "nearest_pct",
    "nearest_side",
    "position_pct",
    "price_source",
    "systime",
]
NUMERIC_HISTORY_COLUMNS = [
    "price",
    "lowlimit",
    "highlimit",
    "distance_low_pct",
    "distance_high_pct",
    "nearest_pct",
    "position_pct",
]


def history_url() -> str:
    """Return the configured rolling history URL.

    The default points to a dedicated ``risk-history`` branch so collector
    updates do not redeploy the Streamlit app from ``main``.
    """
    return os.getenv("MOEX_RISK_HISTORY_URL", DEFAULT_HISTORY_URL).strip()


def empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def normalize_history(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_history()

    result = frame.copy()
    for column in HISTORY_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    result = result[HISTORY_COLUMNS]

    result["captured_at"] = pd.to_datetime(result["captured_at"], errors="coerce", utc=True)
    # SYSTIME may arrive without an explicit timezone. It is kept as text for
    # transparency and duplicate detection rather than interpreted as UTC.
    result["systime"] = result["systime"].astype("string")
    result["assetcode"] = result["assetcode"].astype("string").str.strip()
    result["secid"] = result["secid"].astype("string").str.strip()
    result["nearest_side"] = result["nearest_side"].astype("string").str.strip()
    result["price_source"] = result["price_source"].astype("string").str.strip()
    for column in NUMERIC_HISTORY_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result = result[
        result["captured_at"].notna()
        & result["assetcode"].notna()
        & result["assetcode"].ne("")
        & result["secid"].notna()
        & result["secid"].ne("")
    ]
    return result.sort_values("captured_at", kind="stable").reset_index(drop=True)


def _decode_history_payload(payload: bytes) -> bytes:
    if payload[:2] == b"\x1f\x8b":
        return gzip.decompress(payload)
    return payload


def read_history_payload(payload: bytes) -> pd.DataFrame:
    if not payload:
        return empty_history()
    raw = _decode_history_payload(payload)
    return normalize_history(pd.read_csv(BytesIO(raw)))


def load_risk_history(
    *,
    url: str | None = None,
    timeout: int = 12,
) -> tuple[pd.DataFrame, SourceStatus]:
    target = (url or history_url()).strip()
    if not target:
        return empty_history(), SourceStatus(
            "Risk Radar history", "missing", "MOEX_RISK_HISTORY_URL не задан"
        )

    try:
        response = requests.get(
            target,
            timeout=timeout,
            headers={
                "User-Agent": "MOEX-Risk-Dashboard/1.2 (+rolling boundary history)",
                "Accept": "application/gzip,text/csv,*/*",
                "Cache-Control": "no-cache",
            },
        )
        response.raise_for_status()
        frame = read_history_payload(response.content)
        if frame.empty:
            return frame, SourceStatus(
                "Risk Radar history", "missing", "история пока пуста"
            )
        updated = frame["captured_at"].max()
        updated_text = updated.isoformat() if pd.notna(updated) else ""
        return frame, SourceStatus(
            "Risk Radar history", "live", target, updated_text
        )
    except Exception as exc:
        return empty_history(), SourceStatus(
            "Risk Radar history", "missing", f"история ещё не опубликована: {exc}"
        )


def snapshot_from_monitor(
    monitor_df: pd.DataFrame,
    *,
    captured_at: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Convert the current Risk Radar table into a compact historical snapshot."""
    if monitor_df is None or monitor_df.empty:
        return empty_history()

    timestamp = pd.Timestamp(captured_at or datetime.now(timezone.utc))
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")

    snapshot = pd.DataFrame(index=monitor_df.index)
    snapshot["captured_at"] = timestamp
    mapping = {
        "assetcode": "assetcode",
        "secid": "secid",
        "price": "price",
        "lowlimit": "lowlimit",
        "highlimit": "highlimit",
        "distance_low_pct": "distance_low_pct",
        "distance_high_pct": "distance_high_pct",
        "nearest_pct": "nearest_pct",
        "nearest_side": "nearest_side",
        "position_pct": "position_pct",
        "price_source": "price_source",
        "systime": "systime",
    }
    for destination, source in mapping.items():
        snapshot[destination] = monitor_df[source] if source in monitor_df.columns else pd.NA
    return normalize_history(snapshot)


def _signature(row: pd.Series) -> tuple[object, ...]:
    """Meaningful market-state signature used for de-duplication.

    ``systime`` is deliberately excluded: MOEX may refresh the server timestamp
    even when price and official boundaries are unchanged.  Including it would
    manufacture a fake history point on every collector run.
    """
    values: list[object] = []
    for column in ("price", "lowlimit", "highlimit", "price_source"):
        value = row.get(column)
        if value is None or pd.isna(value):
            values.append(None)
        elif column in {"price", "lowlimit", "highlimit"}:
            values.append(round(float(value), 10))
        else:
            values.append(str(value))
    return tuple(values)


def merge_history(
    existing: pd.DataFrame | None,
    snapshot: pd.DataFrame | None,
    *,
    retention_days: int = 7,
    now: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Append only changed instrument states and keep a rolling time window."""
    old = normalize_history(existing)
    new = normalize_history(snapshot)
    if new.empty:
        merged = old
    elif old.empty:
        merged = new
    else:
        latest = (
            old.sort_values("captured_at", kind="stable")
            .groupby(["assetcode", "secid"], sort=False, as_index=False)
            .tail(1)
        )
        signatures = {
            (str(row["assetcode"]), str(row["secid"])): _signature(row)
            for _, row in latest.iterrows()
        }
        changed_indices: list[int] = []
        for index, row in new.iterrows():
            key = (str(row["assetcode"]), str(row["secid"]))
            if signatures.get(key) != _signature(row):
                changed_indices.append(index)
        additions = new.loc[changed_indices] if changed_indices else empty_history()
        if additions.empty:
            merged = old.copy()
        else:
            merged = pd.concat([old, additions], ignore_index=True)
            merged = normalize_history(merged)

    if merged.empty:
        return merged

    reference = pd.Timestamp(now or datetime.now(timezone.utc))
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")
    else:
        reference = reference.tz_convert("UTC")
    cutoff = reference - pd.Timedelta(days=max(1, int(retention_days)))
    merged = merged[merged["captured_at"] >= cutoff]
    return merged.sort_values(["assetcode", "secid", "captured_at"], kind="stable").reset_index(drop=True)


def write_history_gzip(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_history(frame).copy()
    if not normalized.empty:
        normalized["captured_at"] = normalized["captured_at"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    raw = normalized.to_csv(index=False).encode("utf-8")
    # Deterministic gzip bytes: identical data must produce an identical file.
    path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def history_for_contract(
    history_df: pd.DataFrame,
    assetcode: str,
    secid: str,
    *,
    hours: int = 24,
    now: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    history = normalize_history(history_df)
    if history.empty:
        return history

    rows = history[
        history["assetcode"].astype(str).str.upper().eq(str(assetcode).upper())
        & history["secid"].astype(str).str.upper().eq(str(secid).upper())
    ].copy()
    if rows.empty:
        return rows

    reference = pd.Timestamp(now or datetime.now(timezone.utc))
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")
    else:
        reference = reference.tz_convert("UTC")
    cutoff = reference - pd.Timedelta(hours=max(1, int(hours)))
    return rows[rows["captured_at"] >= cutoff].sort_values("captured_at", kind="stable").reset_index(drop=True)


def history_change_summary(rows: pd.DataFrame) -> dict[str, float | int | None]:
    rows = normalize_history(rows)
    if rows.empty:
        return {"current": None, "minimum": None, "start": None, "change": None, "points": 0}
    nearest = pd.to_numeric(rows["nearest_pct"], errors="coerce").dropna()
    if nearest.empty:
        return {"current": None, "minimum": None, "start": None, "change": None, "points": len(rows)}
    start = float(nearest.iloc[0])
    current = float(nearest.iloc[-1])
    return {
        "current": current,
        "minimum": float(nearest.min()),
        "start": start,
        "change": current - start,
        "points": int(len(rows)),
    }
