from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import gzip

import pandas as pd

from services.history import (
    history_change_summary,
    history_for_contract,
    merge_history,
    read_history_payload,
    snapshot_from_monitor,
)

monitor = pd.DataFrame([
    {
        "assetcode": "AAA",
        "secid": "AAAU6",
        "price": 99.0,
        "lowlimit": 80.0,
        "highlimit": 100.0,
        "distance_low_pct": 19.19,
        "distance_high_pct": 1.01,
        "nearest_pct": 1.01,
        "nearest_side": "HIGH",
        "position_pct": 95.0,
        "price_source": "last",
        "systime": "2026-08-18 10:00:00",
    }
])

t0 = datetime(2026, 8, 18, 7, 0, tzinfo=timezone.utc)
snap0 = snapshot_from_monitor(monitor, captured_at=t0)
assert len(snap0) == 1
assert snap0.iloc[0]["assetcode"] == "AAA"

history = merge_history(pd.DataFrame(), snap0, retention_days=7, now=t0)
assert len(history) == 1

# An identical snapshot must not create noise.
snap_same = snapshot_from_monitor(monitor, captured_at=datetime(2026, 8, 18, 7, 30, tzinfo=timezone.utc))
history_same = merge_history(history, snap_same, retention_days=7, now=datetime(2026, 8, 18, 7, 30, tzinfo=timezone.utc))
assert len(history_same) == 1

# A server timestamp refresh alone is NOT a meaningful market-history point.
monitor_time_only = monitor.copy()
monitor_time_only.loc[0, "systime"] = "2026-08-18 10:30:00"
snap_time_only = snapshot_from_monitor(monitor_time_only, captured_at=datetime(2026, 8, 18, 7, 30, tzinfo=timezone.utc))
history_time_only = merge_history(history_same, snap_time_only, retention_days=7, now=datetime(2026, 8, 18, 7, 30, tzinfo=timezone.utc))
assert len(history_time_only) == 1

# A new price is a real history point.
monitor2 = monitor.copy()
monitor2.loc[0, "price"] = 99.5
monitor2.loc[0, "distance_high_pct"] = 0.50
monitor2.loc[0, "nearest_pct"] = 0.50
monitor2.loc[0, "position_pct"] = 97.5
monitor2.loc[0, "systime"] = "2026-08-18 10:30:00"
snap1 = snapshot_from_monitor(monitor2, captured_at=datetime(2026, 8, 18, 7, 30, tzinfo=timezone.utc))
history2 = merge_history(history_time_only, snap1, retention_days=7, now=datetime(2026, 8, 18, 7, 30, tzinfo=timezone.utc))
assert len(history2) == 2

selected = history_for_contract(history2, "AAA", "AAAU6", hours=24, now=datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc))
assert len(selected) == 2
summary = history_change_summary(selected)
assert summary["minimum"] == 0.5
assert round(float(summary["change"]), 2) == -0.51

# Gzip payload parsing is compatible with the collector output format.
serializable = history2.copy()
serializable["captured_at"] = serializable["captured_at"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
payload = gzip.compress(serializable.to_csv(index=False).encode("utf-8"))
parsed = read_history_payload(payload)
assert len(parsed) == 2
assert parsed["captured_at"].dt.tz is not None

print("Risk Radar rolling history logic OK")
