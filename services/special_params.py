from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Iterable
from urllib.parse import urljoin
from zipfile import ZipFile
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from .loaders import SourceStatus, path_timestamp, fetch_url


CATALOG_URL = "https://www.nationalclearingcentre.ru/catalog/030902"
LINK_TEXT_MARKER = "Календарь применения специальных риск-параметров"
DEFAULT_XLSX_URL = (
    "https://www.nationalclearingcentre.ru/connector?cmd=file&target="
    "B_XNCj0L_SRgNCw0LLQu9C10L3QuNC1INGA0LjRgdC60LDQvNC4XNCh0YDQvtGH0L3Ri9C5INCg0YvQvdC_P0Lpc0JrQsNC70LXQvdC00LDRgNGMINC_S0YDQuNC80LXQvdC10L3QuNGPINGB0L_SQtdGG0LjQsNC70YzQvdGL0YUg0YDQuNGB0Lot0L_SQsNGA0LDQvNC10YLRgNC_P0LIgMjAyNi54bHN4"
)

PARAMETER_META: dict[str, tuple[str, str]] = {
    "AutoShiftNumMR": (
        "Максимальное количество изменений границ ценового коридора",
        "кол-во",
    ),
    "FutMonTimeDay": (
        "Время контроля достаточности границ ценового коридора",
        "сек.",
    ),
    "RangeFut": (
        "Ширина коридора мониторинга",
        "доля",
    ),
    "AutoShiftNumMREvg": (
        "Максимальное количество изменений границ в вечернюю сессию",
        "кол-во",
    ),
    "FutMonTimeEvg": (
        "Время контроля достаточности границ в вечернюю сессию",
        "сек.",
    ),
    "RangeFutEvg": (
        "Ширина коридора мониторинга в вечернюю сессию",
        "доля",
    ),
}

_PARAMETER_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("AutoShiftNumMREvg", ("autoshiftnummrevg", "autoshiftnum m revg", "auto shift num mr evg")),
    ("AutoShiftNumMR", ("autoshiftnummr", "auto shift num mr")),
    ("FutMonTimeEvg", ("futmontimeevg", "fut mon time evg")),
    ("FutMonTimeDay", ("futmontimeday", "fut mon time day")),
    ("RangeFutEvg", ("rangefutevg", "range fut evg")),
    ("RangeFut", ("rangefut", "futrange", "range fut", "fut range")),
]

_DATE_RANGE_RE = re.compile(
    r"с\s*(?:(\d{1,2}:\d{2})\s*)?(\d{1,2}[./]\d{1,2}[./]\d{4})\s*(?:г\.?\s*)?"
    r"по\s*(?:(\d{1,2}:\d{2})\s*)?(\d{1,2}[./]\d{1,2}[./]\d{4})",
    flags=re.IGNORECASE,
)


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            self.links.append((self._href, text))
            self._href = None
            self._text = []


def _http_get(url: str, *, timeout: int = 35, accept: str = "*/*") -> requests.Response:
    payload = fetch_url(
        url,
        timeout=timeout,
        attempts=3,
        accept=accept,
        referer=CATALOG_URL,
    )
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response._content = payload
    response.encoding = "utf-8"
    return response


def discover_special_calendar_url(timeout: int = 35) -> str:
    override = os.getenv("NCC_SPECIAL_RISK_CALENDAR_XLSX_URL", "").strip()
    if override:
        return override

    try:
        response = _http_get(CATALOG_URL, timeout=timeout, accept="text/html,*/*")
        parser = _LinkCollector()
        parser.feed(response.text)
        for href, text in parser.links:
            normalized = text.casefold()
            if LINK_TEXT_MARKER.casefold() in normalized:
                return urljoin(CATALOG_URL, href)
    except Exception:
        # The direct link supplied by NCC remains a useful fallback when the
        # catalogue page is temporarily unavailable or changes its markup.
        pass

    return DEFAULT_XLSX_URL


def _clean_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return " ".join(str(value).replace("\u00a0", " ").split()).strip()


def _compact(value: object) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", _clean_text(value).casefold())


def _canonical_parameter(value: object) -> str | None:
    text = _clean_text(value).casefold()
    compact = _compact(value)
    for parameter, patterns in _PARAMETER_PATTERNS:
        for pattern in patterns:
            if pattern.replace(" ", "") in compact or pattern in text:
                return parameter
    return None


def _is_asset_header(value: object) -> bool:
    compact = _compact(value)
    return compact in {"кодба", "кодбазовогоактива", "базовыйактив", "assetcode"} or (
        "код" in compact and ("ба" in compact or "базовогоактива" in compact)
    )


def _is_title_header(value: object) -> bool:
    compact = _compact(value)
    return any(token in compact for token in ("фьючерсныйконтрактна", "наименованиеба", "описаниеба"))


def _is_period_header(value: object) -> bool:
    compact = _compact(value)
    return "период" in compact and ("действ" in compact or "примен" in compact)


def _parse_number(value: object) -> float | None:
    text = _clean_text(value)
    if not text or text in {"-", "—"}:
        return None
    text = text.replace("%", "").replace(" ", "").replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_datetime(date_text: str, time_text: str | None, *, default_time: str) -> pd.Timestamp:
    date_text = date_text.replace("/", ".")
    clock = time_text or default_time
    return pd.to_datetime(f"{date_text} {clock}", format="%d.%m.%Y %H:%M", errors="raise")


def extract_periods(text: object) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    source = _clean_text(text)
    periods: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for match in _DATE_RANGE_RE.finditer(source):
        start_time, start_date, end_time, end_date = match.groups()
        start_at = _parse_datetime(start_date, start_time, default_time="00:00")
        end_at = _parse_datetime(end_date, end_time, default_time="23:59")
        periods.append((start_at, end_at))
    return periods


def _header_map(row: pd.Series) -> dict[str, int | dict[str, int]] | None:
    asset_index: int | None = None
    title_index: int | None = None
    period_index: int | None = None
    parameters: dict[str, int] = {}

    for index, value in enumerate(row.tolist()):
        if _is_asset_header(value):
            asset_index = index
        elif _is_title_header(value):
            title_index = index
        elif _is_period_header(value):
            period_index = index
        parameter = _canonical_parameter(value)
        if parameter:
            parameters[parameter] = index

    if asset_index is None or not parameters:
        return None
    return {
        "asset": asset_index,
        "title": title_index if title_index is not None else -1,
        "period": period_index if period_index is not None else -1,
        "parameters": parameters,
    }


def _valid_assetcode(value: object) -> str | None:
    text = _clean_text(value).upper()
    if not text or text in {"-", "—", "NAN"}:
        return None
    # Official BA codes are compact Latin/numeric identifiers. Reject prose,
    # dates and section labels so that the parser stops cleanly at the next block.
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,24}", text):
        return None
    return text


def _parse_sheet(sheet_name: str, raw: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    current_periods: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    index = 0

    while index < len(raw):
        row = raw.iloc[index]
        joined = " | ".join(_clean_text(value) for value in row.tolist() if _clean_text(value))
        periods_in_heading = extract_periods(joined)
        if periods_in_heading:
            current_periods = periods_in_heading

        mapping = _header_map(row)
        if mapping is None:
            index += 1
            continue

        asset_index = int(mapping["asset"])
        title_index = int(mapping["title"])
        period_index = int(mapping["period"])
        parameter_map = dict(mapping["parameters"])
        index += 1
        blank_run = 0

        while index < len(raw):
            data_row = raw.iloc[index]
            if _header_map(data_row) is not None:
                # Do not consume the next table header; outer loop will parse it.
                break

            values = data_row.tolist()
            if all(not _clean_text(value) for value in values):
                blank_run += 1
                if blank_run >= 2:
                    break
                index += 1
                continue
            blank_run = 0

            assetcode = _valid_assetcode(values[asset_index] if asset_index < len(values) else None)
            if assetcode is None:
                # A prose row may contain the period for the next table.
                row_periods = extract_periods(" | ".join(_clean_text(value) for value in values))
                if row_periods:
                    current_periods = row_periods
                index += 1
                continue

            title = _clean_text(values[title_index]) if 0 <= title_index < len(values) else ""
            row_periods = []
            if 0 <= period_index < len(values):
                row_periods = extract_periods(values[period_index])
            periods = row_periods or current_periods

            for parameter, column_index in parameter_map.items():
                raw_value = values[column_index] if column_index < len(values) else None
                value_text = _clean_text(raw_value)
                if not value_text or value_text in {"-", "—"}:
                    continue
                parameter_periods = periods or [(pd.NaT, pd.NaT)]
                for start_at, end_at in parameter_periods:
                    title_meta, unit = PARAMETER_META.get(parameter, (parameter, ""))
                    rows.append(
                        {
                            "assetcode": assetcode,
                            "asset_title": title,
                            "parameter": parameter,
                            "parameter_title": title_meta,
                            "value": _parse_number(raw_value),
                            "value_raw": value_text,
                            "unit": unit,
                            "start_at": start_at,
                            "end_at": end_at,
                            "source_sheet": sheet_name,
                            "source_row": index + 1,
                        }
                    )
            index += 1

    return rows




def _xlsx_col_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", str(cell_ref).upper())
    if not match:
        return 0
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def _xlsx_shared_strings(zf: ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in zf.namelist():
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(zf.read(name))
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", ns))
        for item in root.findall("x:si", ns)
    ]


def _xlsx_first_sheet_path(zf: ZipFile) -> str:
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    first_sheet = workbook.find(f"{{{main_ns}}}sheets/{{{main_ns}}}sheet")
    if first_sheet is None:
        raise ValueError("XLSX не содержит листов")
    rel_id = first_sheet.attrib.get(f"{{{rel_ns}}}id")
    if not rel_id:
        return "xl/worksheets/sheet1.xml"
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.findall(f"{{{package_rel_ns}}}Relationship"):
        if rel.attrib.get("Id") == rel_id:
            target = rel.attrib.get("Target", "worksheets/sheet1.xml").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    return "xl/worksheets/sheet1.xml"


def _xlsx_range_bounds(ref: str) -> tuple[int, int, int, int]:
    left, _, right = ref.partition(":")
    right = right or left
    def split(cell: str) -> tuple[int, int]:
        col = _xlsx_col_index(cell)
        row_match = re.search(r"(\d+)$", cell)
        return int(row_match.group(1)) if row_match else 0, col
    r1, c1 = split(left)
    r2, c2 = split(right)
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def _excel_serial_to_timestamp(value: object) -> pd.Timestamp | None:
    try:
        serial = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(serial):
        return None
    return pd.Timestamp("1899-12-30") + pd.to_timedelta(serial, unit="D")


def _read_calendar_matrix(payload: bytes) -> list[dict[str, object]]:
    """Parse the official 2026 matrix calendar with merged status cells.

    The workbook uses dates in row 1, holiday descriptions in row 2 and a
    matrix of Standard/Special states. Parameter values are stored in the
    compact table at the bottom of the same sheet. The parser intentionally
    reads the XLSX XML directly so merged cells and fill styles remain visible.
    """
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(payload)) as zf:
        strings = _xlsx_shared_strings(zf)
        sheet_path = _xlsx_first_sheet_path(zf)
        sheet_root = ET.fromstring(zf.read(sheet_path))
        styles_root = ET.fromstring(zf.read("xl/styles.xml"))

    cell_xfs = styles_root.find("x:cellXfs", ns)
    style_fill_ids = [
        int(xf.attrib.get("fillId", "0"))
        for xf in (list(cell_xfs) if cell_xfs is not None else [])
    ]

    values: dict[tuple[int, int], object] = {}
    styles: dict[tuple[int, int], int] = {}
    for row in sheet_root.findall(".//x:sheetData/x:row", ns):
        row_number = int(row.attrib.get("r", "0"))
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "A1")
            col_number = _xlsx_col_index(ref)
            style_index = int(cell.attrib.get("s", "0"))
            styles[(row_number, col_number)] = style_index
            cell_type = cell.attrib.get("t")
            value: object = None
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//x:t", ns))
            else:
                value_node = cell.find("x:v", ns)
                if value_node is not None and value_node.text is not None:
                    raw = value_node.text
                    if cell_type == "s":
                        index = int(raw)
                        value = strings[index] if 0 <= index < len(strings) else raw
                    else:
                        value = raw
            values[(row_number, col_number)] = value

    merged_anchor: dict[tuple[int, int], tuple[int, int]] = {}
    for merge in sheet_root.findall("x:mergeCells/x:mergeCell", ns):
        r1, c1, r2, c2 = _xlsx_range_bounds(merge.attrib.get("ref", ""))
        for row_number in range(r1, r2 + 1):
            for col_number in range(c1, c2 + 1):
                merged_anchor[(row_number, col_number)] = (r1, c1)

    def resolved(row_number: int, col_number: int) -> tuple[object, int]:
        anchor = merged_anchor.get((row_number, col_number), (row_number, col_number))
        value = values.get(anchor)
        style_index = styles.get(anchor, styles.get((row_number, col_number), 0))
        fill_id = style_fill_ids[style_index] if 0 <= style_index < len(style_fill_ids) else 0
        return value, fill_id

    date_columns: list[tuple[int, pd.Timestamp, str]] = []
    for col_number in range(3, 200):
        raw_date, _ = resolved(1, col_number)
        holiday_date = _excel_serial_to_timestamp(raw_date)
        if holiday_date is None:
            continue
        event_name = _clean_text(resolved(2, col_number)[0])
        date_columns.append((col_number, holiday_date.normalize(), event_name))
    if not date_columns:
        return []

    # Read the value matrix below the calendar. C/D are standard oil/other;
    # E/F are special oil-gas/other. Only special values are emitted.
    special_values: dict[str, dict[str, object]] = {"oil_gas": {}, "other": {}}
    for row_number in range(80, 88):
        parameter = _canonical_parameter(resolved(row_number, 2)[0])
        if not parameter:
            continue
        for category, col_number in (("oil_gas", 5), ("other", 6)):
            raw_value = resolved(row_number, col_number)[0]
            value_text = _clean_text(raw_value)
            if not value_text:
                continue
            if value_text in {"-", "—"}:
                special_values[category][parameter] = (None, "—")
                continue
            number = _parse_number(raw_value)
            if number is None:
                continue
            if parameter == "FutMonTimeDay":
                # The official table is explicitly labelled in minutes.
                number *= 60
                value_text = f"{number:g}"
            special_values[category][parameter] = (number, value_text)

    if not any(special_values.values()):
        return []

    records: list[dict[str, object]] = []
    current_market = ""
    for row_number in range(3, 75):
        raw_code = resolved(row_number, 1)[0]
        raw_title = resolved(row_number, 2)[0]
        title = _clean_text(raw_title)
        code = _valid_assetcode(raw_code)
        if code and "фьючерсный контракт" in title.casefold():
            current_market = code
            continue
        if not code or not title:
            continue

        category = "oil_gas" if re.search(r"нефт|газ", title.casefold()) else "other"
        parameter_values = special_values.get(category, {})
        for col_number, holiday_date, event_name in date_columns:
            state_value, fill_id = resolved(row_number, col_number)
            state_text = _clean_text(state_value).casefold()
            is_special = state_text.startswith("специаль") or fill_id == 2
            if not is_special:
                continue

            start_at = holiday_date - pd.Timedelta(days=1) + pd.Timedelta(hours=23, minutes=50)
            end_at = holiday_date + pd.Timedelta(hours=23, minutes=50)
            for parameter, (number, value_text) in parameter_values.items():
                title_meta, unit = PARAMETER_META.get(parameter, (parameter, ""))
                records.append(
                    {
                        "assetcode": code,
                        "asset_title": title,
                        "parameter": parameter,
                        "parameter_title": title_meta,
                        "value": number,
                        "value_raw": value_text,
                        "unit": unit,
                        "start_at": start_at,
                        "end_at": end_at,
                        "holiday_date": holiday_date,
                        "event_name": event_name,
                        "market_group": current_market,
                        "calendar_state": "Специальные",
                        "source_sheet": "Календарь",
                        "source_row": row_number,
                    }
                )
    return records

def read_special_calendar_xlsx(payload: bytes) -> pd.DataFrame:
    if not payload:
        raise ValueError("пустой XLSX")

    # First try the official matrix form used by the 2026 NCC calendar.
    records = _read_calendar_matrix(payload)

    # Retain support for compact tabular books used in manual uploads/tests.
    if not records:
        sheets = pd.read_excel(BytesIO(payload), sheet_name=None, header=None, dtype=object, engine="openpyxl")
        for sheet_name, raw in sheets.items():
            if raw is None or raw.empty:
                continue
            records.extend(_parse_sheet(str(sheet_name), raw))

    if not records:
        raise ValueError(
            "не найдены матрица календаря или таблицы с колонками «Код БА» и AutoShiftNumMR/FutMonTimeDay/RangeFut"
        )

    frame = pd.DataFrame(records)
    required_columns = [
        "assetcode", "asset_title", "parameter", "parameter_title", "value", "value_raw",
        "unit", "start_at", "end_at", "source_sheet", "source_row",
    ]
    for column in required_columns:
        if column not in frame.columns:
            frame[column] = None
    frame["assetcode"] = frame["assetcode"].astype(str).str.strip().str.upper()
    frame["start_at"] = pd.to_datetime(frame["start_at"], errors="coerce")
    frame["end_at"] = pd.to_datetime(frame["end_at"], errors="coerce")
    if "holiday_date" in frame.columns:
        frame["holiday_date"] = pd.to_datetime(frame["holiday_date"], errors="coerce")
    frame = frame.drop_duplicates(
        subset=["assetcode", "parameter", "value_raw", "start_at", "end_at"],
        keep="last",
    )
    return frame.sort_values(["start_at", "end_at", "assetcode", "parameter"], na_position="last").reset_index(drop=True)


def _read_upload(uploaded_file: BinaryIO | None) -> bytes | None:
    if uploaded_file is None:
        return None
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    return uploaded_file.read()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    Path(temp_name).replace(path)


def _validate_special_calendar(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise ValueError("календарь не содержит строк")
    required = {"assetcode", "parameter", "start_at", "end_at"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("в календаре нет колонок: " + ", ".join(sorted(missing)))
    if not frame["assetcode"].astype(str).str.strip().ne("").any():
        raise ValueError("календарь не содержит кодов БА")


def load_special_calendar_dataset(
    *,
    uploaded_file: BinaryIO | None = None,
    fallback_path: Path | None = None,
    cache_path: Path | None = None,
    manual_path: Path | None = None,
) -> tuple[pd.DataFrame, SourceStatus]:
    name = "Календарь спецпараметров"
    uploaded_payload = _read_upload(uploaded_file)
    upload_error = ""
    if uploaded_payload:
        try:
            frame = read_special_calendar_xlsx(uploaded_payload)
            _validate_special_calendar(frame)
            if manual_path is not None:
                try:
                    _atomic_write(manual_path, uploaded_payload)
                except OSError:
                    pass
            return frame, SourceStatus(
                name, "upload", "загруженный XLSX; проверка пройдена",
                pd.Timestamp.now().strftime("%d.%m.%Y %H:%M:%S"),
            )
        except Exception as exc:
            upload_error = str(exc)

    live_error = ""
    try:
        url = discover_special_calendar_url()
        response = _http_get(
            url,
            accept="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*",
        )
        frame = read_special_calendar_xlsx(response.content)
        _validate_special_calendar(frame)
        if cache_path is not None:
            try:
                _atomic_write(cache_path, response.content)
            except OSError:
                pass
        modified = response.headers.get("Last-Modified", "")
        detail = url + (f"; Last-Modified: {modified}" if modified else "")
        if upload_error:
            detail += f"; ручной файл отклонён: {upload_error}"
        return frame, SourceStatus(
            name, "live", detail, modified or pd.Timestamp.now().strftime("%d.%m.%Y %H:%M:%S")
        )
    except Exception as exc:
        live_error = str(exc)

    cache_error = ""
    if cache_path is not None and cache_path.exists():
        try:
            frame = read_special_calendar_xlsx(cache_path.read_bytes())
            _validate_special_calendar(frame)
            detail = f"последний успешный снимок: {cache_path.name}; live-источник недоступен: {live_error}"
            if upload_error:
                detail += f"; ручной файл отклонён: {upload_error}"
            return frame, SourceStatus(name, "cache", detail, path_timestamp(cache_path))
        except Exception as exc:
            cache_error = str(exc)

    fallback_error = ""
    if fallback_path and fallback_path.exists():
        try:
            frame = read_special_calendar_xlsx(fallback_path.read_bytes())
            _validate_special_calendar(frame)
            detail = f"встроенный снимок: {fallback_path.name}; live-источник недоступен: {live_error}"
            if cache_error:
                detail += f"; кеш недоступен: {cache_error}"
            if upload_error:
                detail += f"; ручной файл отклонён: {upload_error}"
            return frame, SourceStatus(name, "fallback", detail, path_timestamp(fallback_path))
        except Exception as exc:
            fallback_error = str(exc)

    if manual_path is not None and manual_path.exists():
        try:
            frame = read_special_calendar_xlsx(manual_path.read_bytes())
            _validate_special_calendar(frame)
            detail = f"последний сохранённый ручной файл: {manual_path.name}; live-источник недоступен: {live_error}"
            if cache_error:
                detail += f"; кеш недоступен: {cache_error}"
            if fallback_error:
                detail += f"; fallback недоступен: {fallback_error}"
            return frame, SourceStatus(name, "manual", detail, path_timestamp(manual_path))
        except Exception as exc:
            manual_error = str(exc)
    else:
        manual_error = ""

    parts = []
    if upload_error:
        parts.append(f"upload: {upload_error}")
    if live_error:
        parts.append(f"live: {live_error}")
    if cache_error:
        parts.append(f"cache: {cache_error}")
    if fallback_error:
        parts.append(f"fallback: {fallback_error}")
    if manual_error:
        parts.append(f"manual: {manual_error}")
    return pd.DataFrame(), SourceStatus(name, "error", "; ".join(parts) or "источник недоступен")


def _local_naive_timestamp(at: datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(at)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Europe/Moscow").tz_localize(None)
    return ts


def active_special_parameters(
    frame: pd.DataFrame,
    assetcode: str,
    at: datetime | pd.Timestamp,
) -> pd.DataFrame:
    if frame.empty or "assetcode" not in frame.columns:
        return frame.iloc[0:0].copy()
    moment = _local_naive_timestamp(at)
    rows = frame[frame["assetcode"].astype(str).str.upper() == assetcode.upper()].copy()
    if rows.empty:
        return rows
    start_ok = rows["start_at"].notna() & (rows["start_at"] <= moment)
    end_ok = rows["end_at"].notna() & (moment < rows["end_at"])
    return rows[start_ok & end_ok].sort_values(["start_at", "parameter"]).reset_index(drop=True)


def future_special_periods(
    frame: pd.DataFrame,
    assetcode: str,
    at: datetime | pd.Timestamp,
    *,
    limit: int = 10,
) -> pd.DataFrame:
    if frame.empty or "assetcode" not in frame.columns:
        return frame.iloc[0:0].copy()
    moment = _local_naive_timestamp(at)
    rows = frame[
        (frame["assetcode"].astype(str).str.upper() == assetcode.upper())
        & frame["start_at"].notna()
        & (frame["start_at"] > moment)
    ].copy()
    return rows.sort_values(["start_at", "parameter"]).head(limit).reset_index(drop=True)


def calendar_assetcodes(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "assetcode" not in frame.columns:
        return []
    return sorted(
        {value.strip().upper() for value in frame["assetcode"].dropna().astype(str) if value.strip()},
        key=str.upper,
    )


def calendar_wide_view(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    preferred_index = [
        "holiday_date", "start_at", "end_at", "market_group", "event_name",
        "assetcode", "asset_title",
    ]
    index_columns = [column for column in preferred_index if column in frame.columns]
    wide = (
        frame.groupby(index_columns + ["parameter"], dropna=False, sort=False)["value_raw"]
        .last()
        .unstack("parameter")
        .reset_index()
        .rename_axis(columns=None)
    )
    parameter_order = [name for name in PARAMETER_META if name in wide.columns]
    other_columns = [column for column in wide.columns if column not in index_columns + parameter_order]
    return wide[index_columns + parameter_order + other_columns].sort_values(
        ["start_at", "assetcode"], na_position="last"
    ).reset_index(drop=True)
