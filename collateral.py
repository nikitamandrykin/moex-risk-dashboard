from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO
import re
import tempfile

import pandas as pd

from .loaders import SourceStatus, fetch_url, path_timestamp

SECURITIES_URL = "https://www.nationalclearingcentre.ru/rates/securInfo"
ASSETS_URL = "https://www.nationalclearingcentre.ru/rates/assetInfo"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_tag = ""

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._cell_tag = tag

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = " ".join("".join(self._cell).replace("\xa0", " ").split())
            self._row.append(text)
            self._cell = None
            self._cell_tag = ""
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _tables(payload: bytes) -> list[list[list[str]]]:
    text = payload.decode("utf-8", errors="replace")
    parser = _TableParser()
    parser.feed(text)
    return parser.tables


def _clean_code(value: object) -> str:
    return str(value or "").replace("\xa0", " ").strip().upper()


def _yes_no(value: object) -> bool | None:
    text = str(value or "").replace("*", "").strip().casefold()
    if text in {"да", "yes", "1", "true"}:
        return True
    if text in {"нет", "no", "0", "false"}:
        return False
    return None


def _num(value: object) -> float | None:
    text = str(value or "").replace("\xa0", "").replace(" ", "").replace("%", "").replace(",", ".").strip()
    if not text or text in {"-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_security_params_html(payload: bytes) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for table in _tables(payload):
        for row in table:
            if len(row) < 9:
                continue
            code = _clean_code(row[0])
            if not code or code.startswith("{{") or code in {"ТОРГОВЫЙ КОД ЦБ", "ТОРГОВЫЙ КОД"}:
                continue
            isin = str(row[1]).strip() if len(row) > 1 else ""
            # Real security rows contain an ISIN-like value in column 2 and a
            # yes/no short-sale flag in column 4. This rejects Angular template rows.
            short_ban = _yes_no(row[3] if len(row) > 3 else "")
            if short_ban is None and not re.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$", isin.upper()):
                continue
            collateral = _yes_no(row[6] if len(row) > 6 else "")
            records.append(
                {
                    "underlying_code": code,
                    "source_kind": "security",
                    "short_name": str(row[2]).strip() if len(row) > 2 else "",
                    "isin": isin,
                    "short_sale_ban": short_ban,
                    "short_sale_limit": _num(row[4] if len(row) > 4 else None),
                    "collateral_accepted": collateral,
                    "collateral_pool_limit_pct": _num(row[7] if len(row) > 7 else None),
                    "collateral_limit_pct": _num(row[8] if len(row) > 8 else None),
                }
            )
    frame = pd.DataFrame(records)
    if not frame.empty:
        frame = frame.drop_duplicates(subset=["underlying_code"], keep="last").reset_index(drop=True)
    return frame


def parse_asset_params_html(payload: bytes) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for table in _tables(payload):
        for row in table:
            if len(row) < 5:
                continue
            code = _clean_code(row[0])
            if not code or code.startswith("{{") or code in {"ТОРГОВЫЙ КОД", "АКТИВ"}:
                continue
            collateral = _yes_no(row[3] if len(row) > 3 else "")
            # A genuine row has a numeric short limit and/or a yes/no collateral flag.
            short_limit = _num(row[2] if len(row) > 2 else None)
            if collateral is None and short_limit is None:
                continue
            records.append(
                {
                    "underlying_code": code,
                    "source_kind": "currency_metal",
                    "short_name": str(row[1]).strip() if len(row) > 1 else "",
                    "isin": "",
                    # assetInfo publishes the short-sale limit, not a dedicated ban flag.
                    "short_sale_ban": None,
                    "short_sale_limit": short_limit,
                    "collateral_accepted": collateral,
                    "collateral_pool_limit_pct": None,
                    "collateral_limit_pct": _num(row[4] if len(row) > 4 else None),
                }
            )
    frame = pd.DataFrame(records)
    if not frame.empty:
        frame = frame.drop_duplicates(subset=["underlying_code"], keep="last").reset_index(drop=True)
    return frame


def _validate(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        raise ValueError("официальная страница не вернула табличные строки")
    required = {"underlying_code", "short_sale_limit", "collateral_accepted"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("нет обязательных полей: " + ", ".join(sorted(missing)))


def _atomic_frame(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _read_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", dtype={"underlying_code": str}, encoding="utf-8-sig")
    if "underlying_code" in frame.columns:
        frame["underlying_code"] = frame["underlying_code"].astype(str).str.strip().str.upper()
    for col in ("short_sale_limit", "collateral_pool_limit_pct", "collateral_limit_pct"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in ("short_sale_ban", "collateral_accepted"):
        if col in frame.columns:
            frame[col] = frame[col].map(lambda x: _yes_no(x) if not isinstance(x, bool) else x)
    return frame


def load_collateral_page(
    *,
    name: str,
    url: str,
    parser,
    cache_path: Path | None = None,
    fallback_path: Path | None = None,
    timeout: int = 10,
) -> tuple[pd.DataFrame, SourceStatus]:
    live_error = ""
    try:
        payload = fetch_url(url, timeout=timeout, attempts=1, accept="text/html,*/*", referer=url)
        frame = parser(payload)
        _validate(frame)
        if cache_path is not None:
            try:
                _atomic_frame(cache_path, frame)
            except OSError:
                pass
        return frame, SourceStatus(
            name, "live", url, datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S")
        )
    except Exception as exc:
        live_error = str(exc)

    if cache_path is not None and cache_path.exists():
        try:
            frame = _read_frame(cache_path)
            _validate(frame)
            return frame, SourceStatus(
                name, "cache", f"последний успешный снимок; live недоступен: {live_error}", path_timestamp(cache_path)
            )
        except Exception:
            pass

    if fallback_path is not None and fallback_path.exists():
        try:
            frame = _read_frame(fallback_path)
            _validate(frame)
            match = re.search(r"(\d{4})-(\d{2})-(\d{2})", fallback_path.name)
            snapshot_date = f"{match.group(3)}.{match.group(2)}.{match.group(1)}" if match else path_timestamp(fallback_path)
            return frame, SourceStatus(
                name, "fallback", f"встроенный официальный снимок; live недоступен: {live_error}", snapshot_date
            )
        except Exception:
            pass

    return pd.DataFrame(), SourceStatus(name, "error", live_error or "данные не получены", "")


def load_collateral_sources(*, base_dir: Path, data_dir: Path) -> tuple[pd.DataFrame, SourceStatus, pd.DataFrame, SourceStatus]:
    securities, securities_status = load_collateral_page(
        name="Short/Collateral · ценные бумаги",
        url=SECURITIES_URL,
        parser=parse_security_params_html,
        cache_path=base_dir / "runtime_cache" / "security_collateral_last_good.csv",
        fallback_path=None,
    )
    assets, assets_status = load_collateral_page(
        name="Short/Collateral · валюта/металлы",
        url=ASSETS_URL,
        parser=parse_asset_params_html,
        cache_path=base_dir / "runtime_cache" / "asset_collateral_last_good.csv",
        fallback_path=data_dir / "asset_collateral_fallback_2026-07-20.csv",
    )
    return securities, securities_status, assets, assets_status


# Conservative aliases where the futures asset code clearly names the underlying
# currency. Unknown cross-rates/indices are intentionally not mapped.
UNDERLYING_ALIASES = {
    "SI": "USD",
    "USDM": "USD",
    "EU": "EUR",
    "EURM": "EUR",
    "CNY": "CNY",
    "AED": "AED",
    "BYN": "BYN",
    "HKD": "HKD",
    "KZT": "KZT",
    "TRY": "TRY",
    "INR": "INR",
    "PLD": "PLD",
    "PLT": "PLT",
}


def lookup_collateral(assetcode: str, securities: pd.DataFrame, assets: pd.DataFrame) -> pd.Series | None:
    code = _clean_code(assetcode)
    if not code:
        return None

    if securities is not None and not securities.empty and "underlying_code" in securities.columns:
        rows = securities[securities["underlying_code"].astype(str).str.upper() == code]
        if not rows.empty:
            return rows.iloc[-1]

    lookup_code = UNDERLYING_ALIASES.get(code, code)
    if assets is not None and not assets.empty and "underlying_code" in assets.columns:
        rows = assets[assets["underlying_code"].astype(str).str.upper() == lookup_code]
        if not rows.empty:
            return rows.iloc[-1]
    return None
