"""Offline regression for the FastAPI edition: no external HTTP calls."""
from __future__ import annotations
from time import time
from pathlib import Path
import pandas as pd
from fastapi.testclient import TestClient
from services.loaders import SourceStatus
from services.contracts import progressive_position_margin

import web_app

s = SourceStatus("Fixture", "live", "Offline test", "2026-11-20")
market = pd.DataFrame([{
    "assetcode":"RTS","title":"Индекс РТС",
    "mr1":0.13,"mr2":0.20,"mr3":0.27,"lk1":100.0,"lk2":500.0
}])
static = pd.DataFrame([{
    "assetcode":"RTS","autoshiftnummr":20,"futmontime":180,"rangefut":0.5
}])
evening = pd.DataFrame([{
    "assetcode":"RTS","autoshiftnummrevg":0,"futmontimeevg":180
}])
offdays = pd.DataFrame([{
    "assetcode":"RTS","offdaystradingpricerangeshift":0.03,
    "offdays_source":"НКЦ · тест"
}])
contracts = pd.DataFrame([{
    "assetcode":"RTS","secid":"RIZ6","shortname":"RTS-12.26",
    "last":93300.0,"last_rub":153972.0,"lowlimit":86730.0,"highlimit":99270.0,
    "lotvolume":1.0,"minstep":10.0,"stepprice":16.5,"initialmargin":23930.0,
    "lasttradedate":pd.Timestamp("2099-12-15"),
    "tradedate":pd.Timestamp("2026-11-20"),
    "decimals":0
}])
calendar = pd.DataFrame([{
    "assetcode":"RTS","asset_title":"Индекс РТС",
    "parameter":"AutoShiftNumMR","parameter_title":"Общий лимит сдвигов",
    "value":3.0,"value_raw":"3","unit":"кол-во",
    "start_at":pd.Timestamp("2026-12-25 00:00:00"),
    "end_at":pd.Timestamp("2026-12-26 00:00:00"),
    "holiday_date":pd.Timestamp("2026-12-25"),
    "event_name":"test holiday","market_group":"CME"
}])
web_app.store.cached = {
    "market":(market,s),"static":(static,s),"evening":(evening,s),
    "offdays":(offdays,s),"contracts":(contracts,s),"special":(calendar,s),
}
web_app.store.fetched_at = time()
client=TestClient(web_app.app)

def ok(path):
    r=client.get(path)
    assert r.status_code==200,(path,r.status_code,r.text[:500])
    return r

assert ok("/health").json()["framework"]=="FastAPI"
assert "MOEX Risk Dashboard" in ok("/").text
assert "streamlit" not in ok("/").text.lower()
assert ".data-card" in ok("/assets/styles.css").text
assert "loadDashboard" in ok("/assets/app.js").text
boot=ok("/api/bootstrap").json()
assert {"code":"RTS","label":"RTS — Индекс РТС"} in boot["assets"],boot
assert "RTS" in boot["special_assets"]
assert boot["sources"]["market"]["state"]=="live"

view=ok("/api/dashboard?assetcode=RTS&secid=RIZ6&quantity=105").json()
assert view["contract"]["selected"]=="RIZ6",view
assert view["overview"][0]["value"]=="93\u00a0300",view["overview"]
assert view["concentration"][1]["value"]=="100 контр.",view["concentration"]
assert view["boundaries"]["source"]=="LAST"
assert view["boundaries"]["ratio"] is not None
assert view["boundaries"]["shifts"][2]["value"]=="0"
assert view["margin"]["highest"]=="MR2"
expected = progressive_position_margin(105,1,100,500,.13,.2,.27,23930)
assert view["margin"]["total"] == web_app.fmt_rub(expected.total_margin_rub)
assert len(view["margin"]["levels"])==3

override=ok("/api/dashboard?assetcode=RTS&check_at=2026-12-25T12:00:00").json()
assert override["special"]["state"]=="ACTIVE"
assert override["boundaries"]["shifts"][0]["value"]=="3"
assert "SPECIAL" in override["boundaries"]["shifts"][0]["code"]

special=ok("/api/special?assetcode=RTS&check_at=2026-12-25T12:00:00").json()
assert special["state"]=="ACTIVE"
inactive=ok("/api/special?assetcode=RTS&check_at=2026-11-20T12:00:00").json()
assert inactive["state"]=="INACTIVE"
assert ok("/api/methodology").json()["records"][0]["parameter"]=="MR1"
assert ok("/api/special/calendar").json()["rows"]
assert client.get("/api/dashboard?assetcode=UNKNOWN").status_code==404
assert client.get("/api/dashboard?assetcode=RTS&quantity=-10").status_code==422
assert client.get("/api/dashboard?assetcode=RTS&quantity=10000001").status_code==422
assert client.get("/api/special?assetcode=UNKNOWN").status_code==404
assert client.get("/api/special?assetcode=RTS&check_at=not-a-date").status_code==422
assert "import streamlit" not in Path("web_app.py").read_text(encoding="utf-8")
assert "streamlit" not in Path("requirements-web.txt").read_text(encoding="utf-8")
print("FastAPI dashboard: five sections, official source parsers, stepped GO and special calendar — OK")
