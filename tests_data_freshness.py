"""Regression checks for automatic source refresh and honest timestamp labels."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from services.data_freshness import (
    AUTO_REFRESH_POLL_SECONDS,
    SNAPSHOT_TTL_SECONDS,
    fetched_at_label,
    snapshot_expired,
)

assert SNAPSHOT_TTL_SECONDS == 300
assert 0 < AUTO_REFRESH_POLL_SECONDS <= SNAPSHOT_TTL_SECONDS
assert snapshot_expired(None, 100.0)
assert not snapshot_expired(1000.0, 1299.9)
assert snapshot_expired(1000.0, 1300.0)
assert snapshot_expired(1000.0, 1400.0)
assert snapshot_expired(1000.0, 900.0)  # Backward system-clock adjustment.
assert not snapshot_expired(1000.0, 1000.0)

msk = ZoneInfo("Europe/Moscow")
stamp = datetime(2026, 10, 15, 14, 30, tzinfo=msk).timestamp()
assert fetched_at_label(stamp) == "15.10 14:30:00 МСК"
assert fetched_at_label(None) == "—"

app = Path("app.py").read_text(encoding="utf-8")
assert "@st.fragment(run_every=AUTO_REFRESH_POLL_SECONDS)" in app
assert "snapshot_expired(bundle[-1], time())" in app
assert "st.session_state.pop(SOURCE_BUNDLE_KEY, None)" in app
assert 'key="moex_auto_refresh"' in app
assert "contracts_status.updated_at" in app
assert "market_status.updated_at" in app
assert "source_fetched_at" in app
assert "time()," in app
print("Auto refresh and fetch-time display checks OK")
