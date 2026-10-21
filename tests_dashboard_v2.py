from datetime import datetime
from zoneinfo import ZoneInfo
from time import perf_counter
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from services.monitor import build_market_monitor
from services.monitor_cache import cached_market_monitor, monitor_clock
from services.dashboard_ui import source_health_html, selection_key, asset_labels_for_codes, monitor_corridor_html
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

# Grouping a non-empty NCC calendar preserves its per-asset active flags.
live_special = pd.DataFrame([
    {"assetcode": "A2", "parameter": "RangeFut",
     "start_at": pd.Timestamp("2026-10-21 11:00:00"),
     "end_at": pd.Timestamp("2026-10-21 13:00:00"),
     "market_group": "test"},
    {"assetcode": "A3", "parameter": "RangeFut",
     "start_at": pd.Timestamp("2026-10-22 11:00:00"),
     "end_at": pd.Timestamp("2026-10-22 13:00:00"),
     "market_group": "test"},
])
flagged = build_market_monitor(contracts.iloc[:5], empty, live_special, check_at=now)
assert flagged.loc[flagged["assetcode"].eq("A2"), "special_mode"].iloc[0] == True
assert flagged.loc[flagged["assetcode"].eq("A3"), "special_mode"].iloc[0] == False


# Risk Radar drill-down: inside / outside limits, missing data and HTML escaping.
inside = dict(assetcode="RTS", secid="RIZ6", risk_status="WATCH",
              lowlimit=80., price=99., highlimit=100., nearest_pct=1.010101,
              price_source="last", tradedate=pd.Timestamp("2026-10-21"))
card = monitor_corridor_html(inside)
assert "RIZ6" in card and "WATCH" in card and "Цена ISS" in card
assert 'left:95.00%' in card and "1,01% до ближайшей границы" in card
assert "21.10.2026" in card and "время последней сделки" in card
assert "Внутри ценового коридора" in card
outside = {**inside, "price": 110., "risk_status": "OUTSIDE HIGH"}
assert "Выше HIGH" in monitor_corridor_html(outside)
assert 'left:100.00%' in monitor_corridor_html(outside)
unavailable = {**inside, "price": float("nan"), "risk_status": "NORMAL"}
assert "Недостаточно данных" in monitor_corridor_html(unavailable)
assert "Нет данных" in monitor_corridor_html(unavailable)
assert "monitor-corridor-marker" not in monitor_corridor_html(unavailable)
malicious = {**inside, "assetcode": "<script>boom</script>",
             "secid": '<img src=x onerror="boom">'}
markup = monitor_corridor_html(malicious)
assert "<script>" not in markup and "<img" not in markup
assert "&lt;script&gt;" in markup and "&lt;img" in markup


print('Dashboard cache, calendar boundaries, missing-data and selection regressions OK')
