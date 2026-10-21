from datetime import datetime
from zoneinfo import ZoneInfo
from time import perf_counter
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from services.monitor import build_market_monitor
from services.monitor_cache import cached_market_monitor, monitor_clock
from services.dashboard_ui import source_health_html, selection_key, asset_labels_for_codes
from services.loaders import SourceStatus

now = datetime(2026, 10, 21, 12, 0, 20, tzinfo=ZoneInfo('Europe/Moscow'))
contracts = pd.DataFrame([dict(assetcode=f'A{i}', secid=f'A{i}Z6', shortname='Test',
    last=99., lowlimit=80., highlimit=100., lasttradedate=pd.Timestamp('2026-12-15')) for i in range(200)])
empty = pd.DataFrame()
kwargs = dict(check_at=now)
baseline = build_market_monitor(contracts, empty, empty, **kwargs)
cached_market_monitor.clear()
with patch('services.monitor_cache.build_market_monitor', wraps=build_market_monitor) as build:
    start = perf_counter()
    cold = cached_market_monitor(contracts, empty, empty, **kwargs)
    cold_s = perf_counter() - start
    start = perf_counter()
    warm = cached_market_monitor(contracts, empty, empty, **kwargs)
    warm_s = perf_counter() - start
    assert build.call_count == 1
    assert_frame_equal(baseline, cold)
    assert_frame_equal(cold, warm)
    warm.loc[0, 'price'] = 0
    assert cached_market_monitor(contracts, empty, empty, **kwargs).iloc[0].price == 99
    changed = contracts.copy()
    changed.loc[0, 'last'] = 98
    cached_market_monitor(changed, empty, empty, **kwargs)
    cached_market_monitor(contracts, empty, empty, attention_threshold_pct=3., **kwargs)
    assert build.call_count == 3
print(f'Synthetic 200 assets: cold={cold_s:.4f}s warm={warm_s:.4f}s (no network)')

for column, value in [('last', None), ('lowlimit', None), ('highlimit', float('inf')), ('highlimit', 70.)]:
    broken = contracts.iloc[:1].copy()
    broken.loc[0, column] = value
    assert build_market_monitor(broken, empty, empty, **kwargs).iloc[0].risk_status == 'NO DATA'
calendar = pd.DataFrame({'start_at':[pd.Timestamp('2026-10-21 12:00:15')],
                         'end_at':[pd.Timestamp('2026-10-21 12:00:25')]})
assert monitor_clock(calendar, now).second == 15
assert monitor_clock(calendar, now.replace(second=30)).second == 25
assert monitor_clock(calendar, now.replace(second=10)).second == 0
assert monitor_clock(empty, now.replace(day=22, hour=0)).day == 22
key = selection_key(baseline, 1000, 2.)
assert key != selection_key(baseline.iloc[::-1], 1000, 2.)
assert key != selection_key(baseline, 1001, 2.)
markup = source_health_html([SourceStatus('<script>', 'fallback', '', '<date>')], 1000, 1100)
assert '<script>' not in markup and '&lt;script&gt;' in markup
assert 'Резервный набор' in markup
assert 'требует обновления' in source_health_html([], 1000, 1400)

# Underlying labels agree with the established latest_row ordering, while the
# implementation indexes market-rate rows just once.
market_titles = pd.DataFrame([
    {"assetcode": "RTS", "title": "Старое название", "updatetime": pd.Timestamp("2026-10-20")},
    {"assetcode": " RTS ", "title": "Индекс РТС", "updatetime": pd.Timestamp("2026-10-21")},
    {"assetcode": "SBER", "title": "Акции Сбера", "updatetime": pd.Timestamp("2026-10-21")},
])
labels = asset_labels_for_codes(["RTS", "SBER", "UNKNOWN"], market_titles)
assert labels == {"RTS": "RTS — Индекс РТС", "SBER": "SBER — Акции Сбера", "UNKNOWN": "UNKNOWN"}
assert "tradedate" in baseline.columns

print('Dashboard cache, calendar boundaries, missing-data and selection regressions OK')
