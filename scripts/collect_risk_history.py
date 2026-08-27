from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.contracts import load_forts_contracts
from services.history import (
    load_risk_history,
    merge_history,
    snapshot_from_monitor,
    write_history_gzip,
)
from services.monitor import build_market_monitor


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect a rolling Risk Radar boundary snapshot")
    parser.add_argument("--output", required=True, help="Output .csv.gz path")
    parser.add_argument("--history-url", default="", help="Existing rolling history URL")
    parser.add_argument("--retention-days", type=int, default=7)
    args = parser.parse_args()

    contracts, status = load_forts_contracts(timeout=25, cache_path=None)
    if contracts.empty:
        raise RuntimeError(f"MOEX ISS contracts unavailable: {status.detail}")

    monitor = build_market_monitor(
        contracts,
        pd.DataFrame(),
        pd.DataFrame(),
        check_at=datetime.now(timezone.utc),
        attention_threshold_pct=2.0,
        critical_threshold_pct=0.75,
    )
    if monitor.empty:
        raise RuntimeError("Risk Radar snapshot is empty")

    existing = pd.DataFrame()
    if args.history_url:
        existing, _ = load_risk_history(url=args.history_url, timeout=15)

    captured_at = datetime.now(timezone.utc)
    snapshot = snapshot_from_monitor(monitor, captured_at=captured_at)
    merged = merge_history(
        existing,
        snapshot,
        retention_days=args.retention_days,
        now=captured_at,
    )
    output = Path(args.output)
    write_history_gzip(merged, output)

    added = max(0, len(merged) - len(existing))
    print(
        f"Risk history saved: {len(merged)} rows; current monitor={len(monitor)}; "
        f"new/changed rows={added}; source={status.state}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
