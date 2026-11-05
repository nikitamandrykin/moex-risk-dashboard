"""Bounded calculation cache; source frames and calendar transitions are inputs."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from .monitor import build_market_monitor


def monitor_clock(calendar: pd.DataFrame, now: datetime) -> datetime:
    """Reuse within a minute, but never across a special-period boundary."""
    moment = pd.Timestamp(now).tz_convert(ZoneInfo("Europe/Moscow")).tz_localize(None)
    bucket = moment.floor("min")
    for column in ("start_at", "end_at"):
        if column in calendar:
            values = pd.to_datetime(calendar[column], errors="coerce")
            crossed = values[(values > bucket) & (values <= moment)]
            if not crossed.empty:
                bucket = max(bucket, crossed.max())
    return bucket.tz_localize(ZoneInfo("Europe/Moscow")).to_pydatetime()


@st.cache_data(ttl=60, max_entries=32, show_spinner=False)
def cached_market_monitor(contracts_df, market_df, special_calendar_df, *, check_at,
                          attention_threshold_pct=2.0, critical_threshold_pct=0.75):
    return build_market_monitor(
        contracts_df, market_df, special_calendar_df, check_at=check_at,
        attention_threshold_pct=attention_threshold_pct,
        critical_threshold_pct=critical_threshold_pct,
    )
