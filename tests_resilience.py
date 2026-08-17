from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from services import contracts as contracts_module
from services import offdays as offdays_module
from services import evening as evening_module
from services import loaders as loaders_module
from services import special_params as special_module
from services.contracts import load_forts_contracts
from services.offdays import load_offdays_dataset
from services.evening import load_evening_dataset
from services.special_params import load_special_calendar_dataset


class FakeResponse:
    def __init__(self, *, payload=None, content: bytes = b"", url: str = "https://official.test/source"):
        self._payload = payload
        self.content = content
        self.url = url
        self.headers = {"Last-Modified": "Wed, 12 Aug 2026 10:00:00 GMT"}
        self.text = content.decode("utf-8", errors="ignore")

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


sample_iss = {
    "securities": {
        "columns": [
            "SECID", "SHORTNAME", "SECNAME", "ASSETCODE", "PREVSETTLEPRICE",
            "DECIMALS", "MINSTEP", "LASTTRADEDATE", "LASTDELDATE", "LOTVOLUME",
            "INITIALMARGIN", "HIGHLIMIT", "LOWLIMIT", "STEPPRICE", "LASTSETTLEPRICE",
            "IMTIME", "SETTLEPRICE_CLR",
        ],
        "data": [[
            "RIU6", "RTS-9.26", "RTS future", "RTS", 88140, 0, 10,
            "2026-09-17", "2026-09-17", 1, 22361.73, 94180, 82100,
            15.97146, 88140, "2026-08-12 10:00:00", 88140,
        ]],
    },
    "marketdata": {
        "columns": [
            "SECID", "LAST", "SETTLEPRICE", "OPENPOSITION", "UPDATETIME",
            "SYSTIME", "TRADEDATE", "TRADE_SESSION_DATE", "LAST_RUB",
        ],
        "data": [[
            "RIU6", 88780, 88780, 70042, "13:00:00", "2026-08-12 13:00:01",
            "2026-08-12", "2026-08-12", 141795,
        ]],
    },
}

# Contracts: validated live JSON becomes last-good; later outage returns cache.
with TemporaryDirectory() as tmpdir:
    cache = Path(tmpdir) / "contracts.json"
    original_get = contracts_module.requests.get
    original_sleep = contracts_module.time.sleep
    try:
        contracts_module.time.sleep = lambda _: None
        contracts_module.requests.get = lambda *a, **k: FakeResponse(payload=sample_iss)
        live, status = load_forts_contracts(cache_path=cache)
        assert status.state == "live" and cache.exists() and len(live) == 1

        contracts_module.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down"))
        restored, status = load_forts_contracts(cache_path=cache)
        assert status.state == "cache" and restored.iloc[0]["secid"] == "RIU6"
    finally:
        contracts_module.requests.get = original_get
        contracts_module.time.sleep = original_sleep
print("Contracts last-good cache OK")


# OffDays: live XLSX becomes cache; later catalogue/download outage returns cache.
offdays_buffer = BytesIO()
pd.DataFrame([
    ["Код БА", "Фьючерсный контракт ", "Ширина ценового коридора в долях в выходные дни (OffDaysTradingPriceRangeShift)",
     'Ширина величины спреда в долях для инструмента "Календарный спред" в выходные дни (OffDaysTradingRangeCS)'],
    ["CNY", "на курс китайский юань – российский рубль", 0.03, 1.8],
]).to_excel(offdays_buffer, index=False, header=False, engine="openpyxl")
offdays_payload = offdays_buffer.getvalue()

with TemporaryDirectory() as tmpdir:
    cache = Path(tmpdir) / "offdays.xlsx"
    original_discover = offdays_module.discover_offdays_xlsx_url
    original_get = offdays_module._http_get
    try:
        offdays_module.discover_offdays_xlsx_url = lambda timeout=30: "https://official.test/offdays.xlsx"
        offdays_module._http_get = lambda *a, **k: FakeResponse(content=offdays_payload)
        live, status = load_offdays_dataset(cache_path=cache)
        assert status.state == "live" and cache.exists() and len(live) == 1

        offdays_module.discover_offdays_xlsx_url = lambda timeout=30: (_ for _ in ()).throw(RuntimeError("catalog down"))
        restored, status = load_offdays_dataset(cache_path=cache)
        assert status.state == "cache" and restored.iloc[0]["assetcode"] == "CNY"
    finally:
        offdays_module.discover_offdays_xlsx_url = original_discover
        offdays_module._http_get = original_get
print("OffDays last-good cache OK")


# Evening session: official XLSX becomes normalized last-good; outage returns cache.
evening_payload = (Path(__file__).parent / "data" / "evening_static_params_2026-08-03.xlsx").read_bytes()
with TemporaryDirectory() as tmpdir:
    cache = Path(tmpdir) / "evening.csv"
    original_discover = evening_module.discover_evening_xlsx_url
    original_fetch = loaders_module.fetch_url
    try:
        evening_module.discover_evening_xlsx_url = lambda timeout=30: "https://official.test/derivativesStaticParams-03_08_2026.xlsx"
        loaders_module.fetch_url = lambda *a, **k: evening_payload
        live, status = load_evening_dataset(cache_path=cache)
        assert status.state == "live" and cache.exists() and len(live) == 194
        rts = live[live["assetcode"].astype(str).str.upper() == "RTS"].iloc[0]
        assert int(rts["autoshiftnummrevg"]) == 0 and int(rts["futmontimeevg"]) == 180

        evening_module.discover_evening_xlsx_url = lambda timeout=30: "https://official.test/down.xlsx"
        loaders_module.fetch_url = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down"))
        restored, status = load_evening_dataset(cache_path=cache)
        assert status.state == "cache" and len(restored) == 194
    finally:
        evening_module.discover_evening_xlsx_url = original_discover
        loaders_module.fetch_url = original_fetch
print("Evening-params official XLSX -> normalized cache OK")


# Special calendar: validated live XLSX becomes cache; later outage returns it.
base = Path(__file__).parent
special_payload = (base / "data" / "special_risk_calendar_2026.xlsx").read_bytes()
with TemporaryDirectory() as tmpdir:
    cache = Path(tmpdir) / "special.xlsx"
    original_discover = special_module.discover_special_calendar_url
    original_get = special_module._http_get
    try:
        special_module.discover_special_calendar_url = lambda timeout=35: "https://official.test/special.xlsx"
        special_module._http_get = lambda *a, **k: FakeResponse(content=special_payload)
        live, status = load_special_calendar_dataset(cache_path=cache)
        assert status.state == "live" and cache.exists() and not live.empty

        special_module.discover_special_calendar_url = lambda timeout=35: (_ for _ in ()).throw(RuntimeError("catalog down"))
        restored, status = load_special_calendar_dataset(cache_path=cache)
        assert status.state == "cache" and not restored.empty
    finally:
        special_module.discover_special_calendar_url = original_discover
        special_module._http_get = original_get
print("Special-calendar last-good cache OK")

# MR/LK: JSON is accepted, first official transport may fail, normalized cache survives outage.
from services.loaders import load_dataset
import json

sample_limits = {
    "limits": {
        "columns": ["TRADEDATE", "ASSETCODE", "MR1", "MR2", "MR3", "LK1", "LK2", "UPDATETIME"],
        "data": [
            ["2026-08-12", "IMOEX", 0.10, 0.16, 0.22, 2907900, 14539400, "2026-08-10 05:57:38"],
            ["2026-08-12", "CNY", 0.08, 0.10, 0.12, 1500000000, 7500000000, "2026-08-10 05:57:38"],
        ],
    }
}

with TemporaryDirectory() as tmpdir:
    cache = Path(tmpdir) / "market_rates_last_good.csv"
    original_fetch = loaders_module.fetch_url
    calls = []
    try:
        def fake_fetch(url, *args, **kwargs):
            calls.append(url)
            if url.endswith("primary.json"):
                raise RuntimeError("primary transport blocked")
            if url.endswith("secondary.json"):
                return json.dumps(sample_limits).encode("utf-8")
            raise RuntimeError("network down")

        loaders_module.fetch_url = fake_fetch
        live, status = load_dataset(
            name="MR/LK",
            env_url_name="TEST_UNUSED_MR_URL",
            cache_path=cache,
            required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
            default_url="https://official.test/primary.json",
            alternate_urls=["https://official.test/secondary.json"],
        )
        assert status.state == "live"
        assert calls == ["https://official.test/primary.json", "https://official.test/secondary.json"]
        assert live.loc[live.assetcode == "IMOEX", "lk1"].iloc[0] == 2907900
        assert cache.exists()
        cache_text = cache.read_text(encoding="utf-8-sig")
        assert cache_text.startswith("tradedate;assetcode;mr1;mr2;mr3;lk1;lk2;updatetime")

        loaders_module.fetch_url = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("all transports down"))
        restored, status = load_dataset(
            name="MR/LK",
            env_url_name="TEST_UNUSED_MR_URL",
            cache_path=cache,
            required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
            default_url="https://official.test/primary.json",
            alternate_urls=["https://official.test/secondary.json"],
        )
        assert status.state == "cache"
        assert set(restored.assetcode) == {"IMOEX", "CNY"}
    finally:
        loaders_module.fetch_url = original_fetch
print("MR/LK JSON -> alternate official URL -> normalized cache OK")

# --- HTTP transport fallback: requests -> direct requests -> system curl ---
from types import SimpleNamespace

_original_requests_get = loaders_module.requests.get
_original_session = loaders_module.requests.Session
_original_which = loaders_module.shutil.which
_original_run = loaders_module.subprocess.run
try:
    loaders_module.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("python TLS failed"))

    class _FailSession:
        trust_env = True
        def get(self, *a, **k):
            raise RuntimeError("direct python TLS failed")

    loaders_module.requests.Session = _FailSession
    loaders_module.shutil.which = lambda name: "curl.exe" if name in {"curl", "curl.exe"} else None
    loaders_module.subprocess.run = lambda *a, **k: SimpleNamespace(
        returncode=0,
        stdout=b"assetcode;mr1;mr2;mr3;lk1;lk2\nTEST;0.1;0.2;0.3;1;2\n",
        stderr=b"",
    )
    payload = loaders_module.fetch_url("https://official.test/file.csv", attempts=1)
    assert b"TEST" in payload
finally:
    loaders_module.requests.get = _original_requests_get
    loaders_module.requests.Session = _original_session
    loaders_module.shutil.which = _original_which
    loaders_module.subprocess.run = _original_run

print("System HTTPS fallback OK")
