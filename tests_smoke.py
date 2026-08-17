from pathlib import Path
import tempfile

import pandas as pd

from services.loaders import latest_row, read_ncc_csv, read_ncc_xlsx
from services.offdays import read_offdays_fallback
from services.formatters import fmt_compact_rub, weekend_range_details

base = Path(__file__).parent

static_df = read_ncc_csv((base / "data" / "static_params_fallback.csv").read_bytes())
assert "assetcode" in static_df.columns
assert "autoshiftnummr" in static_df.columns
assert len(static_df) > 100
rts_static = latest_row(static_df, "RTS")
assert rts_static is not None
assert int(rts_static["autoshiftnummr"]) == 20
assert isinstance(rts_static["futmontime"], float)
assert isinstance(rts_static["rangefut"], float)

offdays_df = read_offdays_fallback(base / "data" / "offdays_params_fallback.csv")
assert "offdaystradingpricerangeshift" in offdays_df.columns
assert len(offdays_df) > 100
rts_offdays = latest_row(offdays_df, "RTS")
assert rts_offdays is not None
assert abs(float(rts_offdays["offdaystradingpricerangeshift"]) - 0.03) < 1e-12

evening_df = read_ncc_xlsx((base / "data" / "evening_static_params_2026-08-03.xlsx").read_bytes())
assert len(evening_df) == 194
assert int((evening_df["autoshiftnummrevg"] == 5).sum()) == 36
assert int((evening_df["autoshiftnummrevg"] == 2).sum()) == 24
assert int((evening_df["autoshiftnummrevg"] == 0).sum()) == 134
assert "futmontimeevg" in evening_df.columns

br_evening = latest_row(evening_df, "BR")
africa_evening = latest_row(evening_df, "AFRICA")
rts_evening = latest_row(evening_df, "RTS")
assert br_evening is not None and int(br_evening["autoshiftnummrevg"]) == 5
assert int(br_evening["futmontimeevg"]) == 180
assert africa_evening is not None and int(africa_evening["autoshiftnummrevg"]) == 2
assert int(africa_evening["futmontimeevg"]) == 900
assert rts_evening is not None and int(rts_evening["autoshiftnummrevg"]) == 0
assert int(rts_evening["futmontimeevg"]) == 180

details = weekend_range_details(0.03)
assert details["parameter"] == "0,03"
assert details["side"] == "±3%"
assert details["full_width"] == "6%"
assert details["lower_factor"] == "0,97"
assert details["upper_factor"] == "1,03"

print(
    "Smoke test OK:",
    len(static_df),
    "static rows; RTS AutoShiftNumMR =",
    rts_static["autoshiftnummr"],
    ";",
    len(offdays_df),
    "offdays rows; RTS OffDays shift =",
    rts_offdays["offdaystradingpricerangeshift"],
    "; official evening XLSX:",
    "36×5, 24×2, 134×0",
)

# Regression: methodology.csv may contain semicolons inside source notes.
methodology_df = pd.read_csv(base / "data" / "methodology.csv", sep=";", encoding="utf-8-sig", dtype=str)
assert list(methodology_df.columns) == [
    "parameter", "title", "unit", "short_description", "how_to_read", "source_note"
]
assert len(methodology_df) >= 8
assert methodology_df.loc[methodology_df["parameter"] == "AutoShiftNumMREvg", "source_note"].notna().all()

from services.contracts import (
    concentration_limit_to_rub,
    contract_value_rub,
    contracts_for_asset,
    morning_reference_price,
    price_to_rub,
    read_forts_payload,
    reference_price,
    progressive_position_margin,
    simple_position_margin_before_lk1,
    weekend_limits_from_price,
)

sample_iss = {
    "securities": {
        "columns": [
            "SECID", "SHORTNAME", "SECNAME", "ASSETCODE", "PREVSETTLEPRICE",
            "DECIMALS", "MINSTEP", "LASTTRADEDATE", "LASTDELDATE", "LOTVOLUME",
            "INITIALMARGIN", "HIGHLIMIT", "LOWLIMIT", "STEPPRICE", "LASTSETTLEPRICE",
            "IMTIME", "SETTLEPRICE_CLR",
        ],
        "data": [[
            "RIU6", "RTS-9.26", "Фьючерсный контракт RTS-9.26", "RTS", 88140,
            0, 10, "2026-09-17", "2026-09-17", 1,
            22361.73, 94180, 82100, 15.97146, 88140,
            "2026-07-31 07:00:02", 88140,
        ]],
    },
    "marketdata": {
        "columns": [
            "SECID", "LAST", "SETTLEPRICE", "OPENPOSITION", "UPDATETIME",
            "SYSTIME", "TRADEDATE", "TRADE_SESSION_DATE", "LAST_RUB",
        ],
        "data": [[
            "RIU6", 88780, 88780, 70042, "14:44:38",
            "2026-07-31 14:59:38", "2026-07-31", "2026-07-31", 141795,
        ]],
    },
}

contracts_df = read_forts_payload(sample_iss)
assert len(contracts_df) == 1
rts_contracts = contracts_for_asset(contracts_df, "RTS", today=pd.Timestamp("2026-07-31"))
assert len(rts_contracts) == 1
contract = rts_contracts.iloc[0]
price_ref = reference_price(contract)
assert price_ref.field == "last" and price_ref.value == 88780
morning_ref = morning_reference_price(contract)
assert morning_ref.field == "prevsettleprice" and morning_ref.value == 88140
value_rub, value_note = contract_value_rub(contract, price_ref)
assert value_rub == 141795
assert "LAST_RUB" in value_note
assert abs(price_to_rub(88780, 10, 15.97146) - 141794.62188) < 1e-6
weekend_low, weekend_high = weekend_limits_from_price(88780, 0.03)
assert abs(weekend_low - 86116.6) < 1e-9
assert abs(weekend_high - 91443.4) < 1e-9
assert abs(price_to_rub(82100, 10, 15.97146) - 131125.6866) < 1e-6
assert abs(price_to_rub(94180, 10, 15.97146) - 150419.21028) < 1e-6

print("Contract-value tests OK: LAST_RUB, tick conversion and weekend estimates")


# Concentration limits: LK is converted through LOTVOLUME before multiplying
# by the value of one selected futures contract.
example_lk1_rub = concentration_limit_to_rub(13_842, 10, 141_252)
example_lk2_rub = concentration_limit_to_rub(69_207, 10, 141_252)
assert abs(example_lk1_rub - 195_521_018.4) < 1e-6
assert abs(example_lk2_rub - 977_562_716.4) < 1e-6
assert fmt_compact_rub(example_lk1_rub) == "195,52 млн ₽"
assert fmt_compact_rub(example_lk2_rub) == "977,56 млн ₽"
assert fmt_compact_rub(None) == "—"
assert concentration_limit_to_rub(13_842, 0, 141_252) is None
assert concentration_limit_to_rub(None, 10, 141_252) is None
assert concentration_limit_to_rub(13_842, 10, None) is None
print("LK/LOTVOLUME/LAST_RUB tests OK:", fmt_compact_rub(example_lk1_rub), fmt_compact_rub(example_lk2_rub))

# Simple one-series margin calculator: calculate only while the position stays
# within the first concentration limit.
margin_rub, position_ba, within_lk1 = simple_position_margin_before_lk1(
    100,
    10,
    13_842,
    22_361.73,
)
assert position_ba == 1_000
assert within_lk1 is True
assert abs(margin_rub - 2_236_173) < 1e-9

margin_rub, position_ba, within_lk1 = simple_position_margin_before_lk1(
    1_385,
    10,
    13_842,
    22_361.73,
)
assert position_ba == 13_850
assert within_lk1 is False
assert margin_rub is None
assert simple_position_margin_before_lk1(1, 0, 13_842, 22_361.73) == (None, None, None)
print("GO-before-LK1 tests OK: calculation stops when POSITION_BA exceeds LK1")

# Progressive concentration calculator: only the marginal part above each LK
# threshold is scaled to the next MR level.
progressive = progressive_position_margin(
    320,
    100,
    10_000,
    25_000,
    0.10,
    0.15,
    0.20,
    15_000,
)
assert progressive.error is None
assert progressive.highest_level == 3
assert abs(progressive.level1_contracts - 100) < 1e-12
assert abs(progressive.level2_contracts - 150) < 1e-12
assert abs(progressive.level3_contracts - 70) < 1e-12
assert abs(progressive.level2_multiplier - 1.5) < 1e-12
assert abs(progressive.level3_multiplier - 2.0) < 1e-12
assert abs(progressive.level1_margin_rub - 1_500_000) < 1e-9
assert abs(progressive.level2_margin_rub - 3_375_000) < 1e-9
assert abs(progressive.level3_margin_rub - 2_100_000) < 1e-9
assert abs(progressive.total_margin_rub - 6_975_000) < 1e-9

# Higher-level data is not required while the position remains within LK1.
within_first_level = progressive_position_margin(50, 100, 10_000, None, 0.10, None, None, 15_000)
assert within_first_level.error is None
assert within_first_level.highest_level == 1
assert abs(within_first_level.total_margin_rub - 750_000) < 1e-9

missing_second_level = progressive_position_margin(101, 100, 10_000, None, 0.10, None, None, 15_000)
assert missing_second_level.total_margin_rub is None
assert missing_second_level.error is not None
print("Progressive GO tests OK: 100×MR1, 150×MR2, 70×MR3 =", fmt_compact_rub(progressive.total_margin_rub))


from datetime import datetime
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from services.boundaries import (
    estimate_morning_limits,
    is_currency_future,
    is_morning_session,
    read_morning_snapshot,
    save_morning_snapshot,
)

assert is_currency_future("CNY", "на курс китайский юань – российский рубль")
assert is_currency_future("Si", "")
assert not is_currency_future("RTS", "Фьючерсный контракт на индекс РТС")
moscow = ZoneInfo("Europe/Moscow")
assert is_morning_session(datetime(2026, 8, 4, 7, 30, tzinfo=moscow))
assert not is_morning_session(datetime(2026, 8, 4, 10, 0, tzinfo=moscow))
assert not is_morning_session(datetime(2026, 8, 8, 7, 30, tzinfo=moscow))
morning_estimate = estimate_morning_limits(
    current_low=82100, current_high=94180, reference_price=88140, offdays_shift=0.03
)
assert morning_estimate.error is None
assert morning_estimate.auto_shift_count == 0
assert abs(morning_estimate.offdays_low - 85495.8) < 1e-9
assert abs(morning_estimate.offdays_high - 90784.2) < 1e-9
assert abs(morning_estimate.effective_low - 85495.8) < 1e-9
assert abs(morning_estimate.effective_high - 90784.2) < 1e-9
with TemporaryDirectory() as tmp_dir:
    snapshot_path = Path(tmp_dir) / "morning.json"
    saved = save_morning_snapshot(
        snapshot_path,
        secid="CRU6",
        assetcode="CNY",
        low_quote=11.5,
        high_quote=13.5,
        low_rub=11500,
        high_rub=13500,
        source_time="04.08.2026 07:30:00",
    )
    assert saved is not None
    restored = read_morning_snapshot(snapshot_path, "CRU6")
    assert restored is not None and restored["low_quote"] == 11.5 and restored["high_quote"] == 13.5
print("Morning-boundary tests OK: currency filter, zero auto shifts and weekend-parameter estimate")

# Stable ISS CSV parsing and last-good cache fallback.
from services import loaders as loaders_module
from services.loaders import load_dataset, read_ncc_csv

iss_limits_payload = b'''limits
TRADEDATE;ASSETCODE;MR1;MR2;MR3;LK1;LK2;UPDATETIME
05.08.2026;RTS;0,13;0,20;0,27;13842;69210;05.08.2026 19:00:00
'''
parsed_limits = read_ncc_csv(iss_limits_payload)
assert list(parsed_limits[["assetcode", "mr1", "lk1"]].iloc[0]) == ["RTS", 0.13, 13842.0]

with tempfile.TemporaryDirectory() as tmpdir:
    cache_path = Path(tmpdir) / "market_rates_last_good.csv"
    original_fetch = loaders_module.fetch_url
    try:
        loaders_module.fetch_url = lambda url: iss_limits_payload
        live_df, live_status = load_dataset(
            name="MR/LK",
            env_url_name="_TEST_MR_URL_",
            default_url="https://example.test/limits.csv",
            cache_path=cache_path,
            required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
        )
        assert live_status.state == "live" and cache_path.exists() and not live_df.empty
        loaders_module.fetch_url = lambda url: (_ for _ in ()).throw(RuntimeError("network down"))
        cached_df, cached_status = load_dataset(
            name="MR/LK",
            env_url_name="_TEST_MR_URL_",
            default_url="https://example.test/limits.csv",
            cache_path=cache_path,
            required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
        )
        assert cached_status.state == "cache" and cached_df.iloc[0]["assetcode"] == "RTS"
    finally:
        loaders_module.fetch_url = original_fetch
print("MR/LK source tests OK: stable ISS CSV and last-good cache")

# Manual MR/LK XLSX: validate required columns, persist normalized copy and
# keep good automatic/cache data when a new upload is invalid.
from io import BytesIO as _BytesIO
from services.loaders import read_ncc_xlsx

class NamedBytesIO(_BytesIO):
    def __init__(self, payload: bytes, name: str):
        super().__init__(payload)
        self.name = name

xlsx_buffer = _BytesIO()
pd.DataFrame([
    ["assetcode", "mr1", "mr2", "mr3", "lk1", "lk2", "updatetime"],
    ["RTS", "13%", "20%", "27%", 13842, 69210, "12.08.2026 12:00:00"],
]).to_excel(xlsx_buffer, index=False, header=False, engine="openpyxl")
manual_xlsx = NamedBytesIO(xlsx_buffer.getvalue(), "official_mr_lk.xlsx")
parsed_xlsx = read_ncc_xlsx(xlsx_buffer.getvalue())
assert abs(float(parsed_xlsx.iloc[0]["mr1"]) - 0.13) < 1e-12

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    manual_path = tmp / "market_rates_manual.csv"
    uploaded_df, uploaded_status = load_dataset(
        name="MR/LK", env_url_name="_NO_URL_", uploaded_file=manual_xlsx,
        manual_path=manual_path,
        required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
    )
    assert uploaded_status.state == "upload" and manual_path.exists()
    assert abs(float(uploaded_df.iloc[0]["mr3"]) - 0.27) < 1e-12

    # With no network and no cache/fallback, the persisted manual copy survives restart.
    restored_df, restored_status = load_dataset(
        name="MR/LK", env_url_name="_NO_URL_", manual_path=manual_path,
        required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
    )
    assert restored_status.state == "manual" and restored_df.iloc[0]["assetcode"] == "RTS"

    # An invalid upload must not blank a valid last-good cache.
    cache_path = tmp / "last_good.csv"
    cache_path.write_bytes(iss_limits_payload)
    bad_upload = NamedBytesIO(b"assetcode;mr1\nRTS;0.13\n", "bad.csv")
    original_fetch = loaders_module.fetch_url
    try:
        loaders_module.fetch_url = lambda url: (_ for _ in ()).throw(RuntimeError("network down"))
        safe_df, safe_status = load_dataset(
            name="MR/LK", env_url_name="_TEST_", default_url="https://example.test/limits.csv",
            cache_path=cache_path, manual_path=manual_path, uploaded_file=bad_upload,
            required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
        )
        assert safe_status.state == "cache" and not safe_df.empty
        assert "ручной файл отклонён" in safe_status.detail
    finally:
        loaders_module.fetch_url = original_fetch
print("Manual MR/LK resilience tests OK: XLSX, persistence, bad-upload protection")

app_text = (base / "app.py").read_text(encoding="utf-8")
assert "LK1 ÷ LOTVOLUME × LAST_RUB" in app_text
assert "reference_tab, formulas_tab" in app_text
assert "Нижняя оценка стоимости" not in app_text
assert "Верхняя оценка стоимости" not in app_text
assert "Оценка стоимости контракта при движении цены" not in app_text
assert '<div class="metric-note">' in app_text
assert "Технические значения: все используемые параметры" in app_text
assert "LK1_RUB_EQUIVALENT" in app_text
assert "N_{LK1}" in app_text
assert "methodology-table-wrap" in app_text
assert "HIGHLIMIT_RUB" in app_text
assert "Калькулятор ГО по уровням концентрации" in app_text
assert "GO_{position}=N_1IM_1+N_2IM_2+N_3IM_3" in app_text
assert "NAV_PAGES = [\"Обзор\", \"Границы\", \"Калькулятор ГО\", \"Спецрежимы НКЦ\", \"Методика\"]" in app_text
assert "if active_page == \"Обзор\":" in app_text
assert "load_all_sources_cached" in app_text and "SOURCE_BUNDLE_KEY" in app_text
assert "Количество контрактов" in app_text
assert "MOEX Risk Dashboard" in app_text
assert "Специальные риск-параметры для выбранного БА" not in app_text
assert "Экономика контракта" in app_text
assert "Цена шага" in app_text
assert "1 пункт котировки" in app_text
assert "Зарубежная площадка" in app_text

# GO is now isolated in the dedicated tab and methodology formulas.
assert "Расчётное ГО позиции" not in app_text
assert 'add_technical_row("Калькулятор ГО"' not in app_text
assert 'add_technical_row("Контракт", "INITIALMARGIN"' not in app_text
assert "Как считается." not in app_text
assert "Логика расчёта." not in app_text

# Current limits are separated from the contract-value cards. Morning limits
# are conditional on the currency-underlying classifier.
assert "Границы и механизм автоматических раздвижек" in app_text
assert "Аналитическая оценка утренних границ" in app_text
assert "if currency_future:" in app_text
assert "estimate_morning_limits" in app_text
assert "утреннюю дополнительную сессию: 0" in app_text
assert "OffDaysTradingPriceRangeShift" in app_text
assert "Мониторинг и раздвижки" in app_text
assert "special_override" in app_text
assert "FutMonTimeDay" in app_text and "RangeFut" in app_text
assert "Календарь специальных риск-параметров НКЦ" in app_text
# Lazy navigation regression: Methodology must not depend on variables that are
# created only when the GO calculator page has already been rendered.
methodology_source = app_text.split('if active_page == "Методика":', 1)[1]
assert "calculator_position_contracts" not in methodology_source
assert "calculator_position_margin" not in methodology_source
assert "methodology_position_contracts" in methodology_source
assert "methodology_position_margin" in methodology_source
print("Presentation UI tests OK: overview, boundaries, active overrides, morning estimate, dedicated GO tab and lazy-page independence")


# Special-risk calendar XLSX: periods, asset codes and parameter values.
from io import BytesIO
from services.special_params import (
    active_special_parameters,
    calendar_assetcodes,
    calendar_wide_view,
    extract_periods,
    future_special_periods,
    read_special_calendar_xlsx,
)

periods = extract_periods("с 19:00 17.04.2026 г. по 19:00 18.04.2026 г.")
assert len(periods) == 1
assert periods[0][0] == pd.Timestamp("2026-04-17 19:00")
assert periods[0][1] == pd.Timestamp("2026-04-18 19:00")

calendar_rows = [
    ["Календарь применения специальных риск-параметров", None, None, None, None],
    ["Период действия с 19:00 17.04.2026 по 19:00 18.04.2026", None, None, None, None],
    ["Код БА", "Фьючерсный контракт на", "AutoShiftNumMR", "FutMonTimeDay", "RangeFut"],
    ["BR", "нефть Brent", 0, "-", 0.3],
    ["SPYF", "SPY ETF", 2, 1800, "-"],
]
buffer = BytesIO()
pd.DataFrame(calendar_rows).to_excel(buffer, index=False, header=False, engine="openpyxl")
special_df = read_special_calendar_xlsx(buffer.getvalue())
assert set(calendar_assetcodes(special_df)) == {"BR", "SPYF"}
assert len(special_df) == 4
active_spyf = active_special_parameters(
    special_df, "SPYF", datetime(2026, 4, 18, 12, 0, tzinfo=moscow)
)
assert set(active_spyf["parameter"]) == {"AutoShiftNumMR", "FutMonTimeDay"}
assert active_special_parameters(
    special_df, "SPYF", datetime(2026, 4, 19, 12, 0, tzinfo=moscow)
).empty
assert future_special_periods(
    special_df, "SPYF", datetime(2026, 4, 16, 12, 0, tzinfo=moscow)
).shape[0] == 2
wide_special = calendar_wide_view(special_df)
assert len(wide_special) == 2
assert "AutoShiftNumMR" in wide_special.columns
assert "Спецрежимы НКЦ" in app_text
assert "Календарь специальных параметров" in app_text
assert "load_special_calendar_cached.clear()" in app_text

official_calendar = read_special_calendar_xlsx((base / "data" / "special_risk_calendar_2026.xlsx").read_bytes())
assert official_calendar["assetcode"].nunique() >= 50
assert {"BR", "SPYF", "NG", "DAX"}.issubset(set(calendar_assetcodes(official_calendar)))
active_br = active_special_parameters(
    official_calendar, "BR", datetime(2026, 4, 3, 12, 0, tzinfo=moscow)
)
assert set(active_br["parameter"]) == {"AutoShiftNumMR", "AutoShiftNumMREvg", "FutMonTimeDay", "RangeFut"}
assert float(active_br.loc[active_br["parameter"] == "RangeFut", "value"].iloc[0]) == 0.3
assert active_br.loc[active_br["parameter"] == "FutMonTimeDay", "value_raw"].iloc[0] == "—"
active_spyf_official = active_special_parameters(
    official_calendar, "SPYF", datetime(2026, 2, 16, 12, 0, tzinfo=moscow)
)
assert set(active_spyf_official["parameter"]) == {"AutoShiftNumMR", "AutoShiftNumMREvg", "FutMonTimeDay", "RangeFut"}
assert float(active_spyf_official.loc[active_spyf_official["parameter"] == "FutMonTimeDay", "value"].iloc[0]) == 1800
assert active_spyf_official.loc[active_spyf_official["parameter"] == "RangeFut", "value_raw"].iloc[0] == "—"
assert active_spyf_official["event_name"].str.contains("Washington", case=False).all()
print("Special-risk calendar tests OK: compact uploads and official merged-cell 2026 matrix")
