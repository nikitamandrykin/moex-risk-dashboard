from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from services.monitor import build_market_monitor, classify_asset_group
from services.collateral import parse_security_params_html, parse_asset_params_html, lookup_collateral

contracts = pd.DataFrame([
    {"assetcode":"AAA","secid":"AAA-9.26","shortname":"на акции AAA","last":99.0,"lowlimit":80.0,"highlimit":100.0,"lasttradedate":pd.Timestamp("2026-09-15"),"openposition":100},
    {"assetcode":"BBB","secid":"BBB-9.26","shortname":"на индекс BBB","last":81.0,"lowlimit":80.0,"highlimit":100.0,"lasttradedate":pd.Timestamp("2026-09-15"),"openposition":50},
    {"assetcode":"CCC","secid":"CCC-9.26","shortname":"на акции CCC","last":90.0,"lowlimit":80.0,"highlimit":100.0,"lasttradedate":pd.Timestamp("2026-09-15"),"openposition":50},
    {"assetcode":"DDD","secid":"DDD-9.26","shortname":"на акции DDD","last":99.5,"lowlimit":99.0,"highlimit":100.0,"lasttradedate":pd.Timestamp("2026-09-15"),"openposition":25},
])
market = pd.DataFrame([
    {"assetcode":"AAA","title":"на акции AAA"},
    {"assetcode":"BBB","title":"на индекс BBB"},
    {"assetcode":"CCC","title":"на акции CCC"},
    {"assetcode":"DDD","title":"на акции DDD"},
])
special = pd.DataFrame(columns=["assetcode","parameter","start_at","end_at"])
radar = build_market_monitor(
    contracts, market, special,
    check_at=datetime(2026,8,17,12,0,tzinfo=ZoneInfo("Europe/Moscow")),
    attention_threshold_pct=2.0,
    critical_threshold_pct=0.75,
)
assert len(radar) == 4
assert radar.iloc[0]["assetcode"] == "DDD"
assert set(radar[radar["risk_status"].isin(["WATCH","CRITICAL"])]["assetcode"]) == {"AAA","BBB","DDD"}
assert radar.loc[radar.assetcode.eq("AAA"), "nearest_side"].iloc[0] == "HIGH"
assert radar.loc[radar.assetcode.eq("BBB"), "nearest_side"].iloc[0] == "LOW"
assert radar.loc[radar.assetcode.eq("DDD"), "nearest_side"].iloc[0] == "CENTER"
assert radar.loc[radar.assetcode.eq("DDD"), "price_source"].iloc[0] == "last"
assert classify_asset_group("BBB", "на индекс BBB") == "Индексы"
assert classify_asset_group("AAA", "на акции AAA") == "Акции"

security_html = '''<table><tr><td>SBER</td><td>RU0009029540</td><td>Сбербанк</td><td>Нет</td><td>123</td><td>Да</td><td>Да</td><td>100</td><td>90</td></tr></table>'''.encode('utf-8')
sec = parse_security_params_html(security_html)
assert len(sec) == 1
assert sec.iloc[0]["underlying_code"] == "SBER"
assert sec.iloc[0]["short_sale_ban"] == False
assert sec.iloc[0]["collateral_accepted"] == True
assert sec.iloc[0]["collateral_limit_pct"] == 90

asset_html = '''<table><tr><td>CNY</td><td>CNY</td><td>0</td><td>Да</td><td>100</td></tr></table>'''.encode('utf-8')
assets = parse_asset_params_html(asset_html)
assert len(assets) == 1
info = lookup_collateral("CNY", sec, assets)
assert info is not None and info["collateral_accepted"] == True
print("Market Monitor logic and collateral parsers OK")
