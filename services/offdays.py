from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO
from datetime import datetime
import tempfile
import time
from urllib.parse import urljoin
from zipfile import ZipFile
import csv
import os
import re
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from .loaders import SourceStatus, read_ncc_csv, path_timestamp, fetch_url, BROWSER_USER_AGENT


CATALOG_URL = "https://www.nationalclearingcentre.ru/catalog/030902"
LINK_TEXT_MARKER = "Ширина ценового коридора в долях для каждого БА при торгах в выходные дни"

DEFAULT_XLSX_URL = (
    "https://www.nationalclearingcentre.ru/connector?"
    "_t=1784302792&cmd=file&target="
    "B_XNCj0L_SRgNCw0LLQu9C10L3QuNC1INGA0LjRgdC60LDQvNC4XNCh0YDQvtGH0L3Ri9C5INCg0YvQvdC_P0Lpc"
    "0KDQuNGB0Lot0L_SQsNGA0LDQvNC10YLRgNGLXFNDLTUwNDYyMFwyMDI2MDcxNyDQqNC40YDQuNC90LAg0YbQtdC9"
    "0L7QstC_P0LPQviDQutC_P0YDQuNC00L7RgNCwINCyINC00L7Qu9GP0YUg0LIg0LLRi9GF0L7QtNC90YvQtSDQtNC90LgueGxzeA_E_E"
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


def _http_get(url: str, *, timeout: int = 30, accept: str = "*/*") -> requests.Response:
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


def discover_offdays_xlsx_url(timeout: int = 30) -> str:
    override = os.getenv("NCC_OFFDAYS_XLSX_URL", "").strip()
    if override:
        return override

    try:
        response = _http_get(CATALOG_URL, timeout=timeout, accept="text/html,*/*")
        parser = _LinkCollector()
        parser.feed(response.text)

        for href, text in parser.links:
            normalized = text.casefold()
            if LINK_TEXT_MARKER.casefold() in normalized or "offdaystradingpricerangeshift" in normalized:
                return urljoin(CATALOG_URL, href)
    except Exception:
        pass

    # Current direct NCC connector link. The catalogue remains primary so a
    # future filename update is discovered automatically when it is reachable.
    return DEFAULT_XLSX_URL


def _col_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref.upper())
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(zf: ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in zf.namelist():
        return []
    root = ET.fromstring(zf.read(name))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    result: list[str] = []
    for item in root.findall("x:si", ns):
        text = "".join(node.text or "" for node in item.findall(".//x:t", ns))
        result.append(text)
    return result


def _first_sheet_path(zf: ZipFile) -> str:
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
            target = rel.attrib.get("Target", "worksheets/sheet1.xml")
            target = target.lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    return "xl/worksheets/sheet1.xml"


def read_offdays_xlsx(payload: bytes) -> pd.DataFrame:
    """Parse the official NCC XLSX without an Excel engine dependency."""
    with ZipFile(BytesIO(payload)) as zf:
        strings = _shared_strings(zf)
        sheet_path = _first_sheet_path(zf)
        root = ET.fromstring(zf.read(sheet_path))

    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[Any]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values: dict[int, Any] = {}
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "A1")
            idx = _col_index(ref)
            cell_type = cell.attrib.get("t")
            value_node = cell.find("x:v", ns)
            value: Any = None
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//x:t", ns))
            elif value_node is not None and value_node.text is not None:
                raw = value_node.text
                if cell_type == "s":
                    shared_idx = int(raw)
                    value = strings[shared_idx] if 0 <= shared_idx < len(strings) else raw
                elif cell_type in {"str", "b"}:
                    value = raw
                else:
                    try:
                        value = float(raw)
                    except ValueError:
                        value = raw
            values[idx] = value
        if values:
            max_idx = max(values)
            rows.append([values.get(i) for i in range(max_idx + 1)])

    if not rows:
        return pd.DataFrame()

    headers = [str(value or "").strip() for value in rows[0]]
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        padded = row + [None] * max(0, len(headers) - len(row))
        raw_record = dict(zip(headers, padded))
        assetcode = str(raw_record.get("Код БА") or "").strip()
        if not assetcode:
            continue
        records.append(
            {
                "assetcode": assetcode,
                "title": str(raw_record.get("Фьючерсный контракт ") or raw_record.get("Фьючерсный контракт") or "").strip(),
                "offdaystradingpricerangeshift": raw_record.get(
                    "Ширина ценового коридора в долях в выходные дни (OffDaysTradingPriceRangeShift)"
                ),
                "offdaystradingrangecs": raw_record.get(
                    'Ширина величины спреда в долях для инструмента "Календарный спред" в выходные дни (OffDaysTradingRangeCS)'
                ),
            }
        )

    df = pd.DataFrame(records)
    for col in ("offdaystradingpricerangeshift", "offdaystradingrangecs"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def read_offdays_fallback(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", dtype={"assetcode": str})
    for col in ("offdaystradingpricerangeshift", "offdaystradingrangecs"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "assetcode" in df.columns:
        df["assetcode"] = df["assetcode"].astype(str).str.strip()
    return df


def _validate_offdays(df: pd.DataFrame) -> None:
    if df.empty:
        raise ValueError("источник OffDays не содержит строк")
    required = {"assetcode", "offdaystradingpricerangeshift"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("нет обязательных колонок: " + ", ".join(sorted(missing)))
    if not df["offdaystradingpricerangeshift"].notna().any():
        raise ValueError("OffDaysTradingPriceRangeShift не содержит пригодных значений")


def _read_upload(uploaded_file: BinaryIO | None) -> tuple[bytes | None, str]:
    if uploaded_file is None:
        return None, ""
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    return uploaded_file.read(), str(getattr(uploaded_file, "name", "") or "")


def _read_offdays_payload(payload: bytes, filename: str = "") -> pd.DataFrame:
    if Path(filename).suffix.lower() in {".xlsx", ".xlsm"} or payload[:2] == b"PK":
        return read_offdays_xlsx(payload)
    return read_ncc_csv(payload)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    Path(temp_name).replace(path)


def _save_manual_csv(path: Path, df: pd.DataFrame) -> None:
    payload = df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    _atomic_write(path, payload)


def load_offdays_dataset(
    *,
    fallback_path: Path | None = None,
    cache_path: Path | None = None,
    manual_path: Path | None = None,
    uploaded_file: BinaryIO | None = None,
    timeout: int = 30,
) -> tuple[pd.DataFrame, SourceStatus]:
    name = "Выходной коридор"
    uploaded_payload, uploaded_name = _read_upload(uploaded_file)
    upload_error = ""
    if uploaded_payload:
        try:
            df = _read_offdays_payload(uploaded_payload, uploaded_name)
            _validate_offdays(df)
            if manual_path is not None:
                try:
                    _save_manual_csv(manual_path, df)
                except OSError:
                    pass
            return df, SourceStatus(
                name, "upload", f"ручной файл {uploaded_name or 'upload'}; проверка пройдена",
                datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S"),
            )
        except Exception as exc:
            upload_error = str(exc)

    live_error = ""
    try:
        url = discover_offdays_xlsx_url(timeout=timeout)
        response = _http_get(
            url,
            timeout=timeout,
            accept="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
        )
        df = read_offdays_xlsx(response.content)
        _validate_offdays(df)
        if cache_path is not None:
            try:
                _atomic_write(cache_path, response.content)
            except OSError:
                pass
        modified = response.headers.get("Last-Modified", "")
        detail = url + (f"; Last-Modified: {modified}" if modified else "")
        if upload_error:
            detail += f"; ручной файл отклонён: {upload_error}"
        return df, SourceStatus(
            name, "live", detail, modified or datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S")
        )
    except Exception as exc:
        live_error = str(exc)

    cache_error = ""
    if cache_path is not None and cache_path.exists():
        try:
            df = _read_offdays_payload(cache_path.read_bytes(), cache_path.name)
            _validate_offdays(df)
            detail = f"последний успешный снимок: {cache_path.name}; live-источник недоступен: {live_error}"
            if upload_error:
                detail += f"; ручной файл отклонён: {upload_error}"
            return df, SourceStatus(name, "cache", detail, path_timestamp(cache_path))
        except Exception as exc:
            cache_error = str(exc)

    fallback_error = ""
    if fallback_path and fallback_path.exists():
        try:
            df = read_offdays_fallback(fallback_path)
            _validate_offdays(df)
            detail = f"встроенный снимок: {fallback_path.name}; live-источник недоступен: {live_error}"
            if cache_error:
                detail += f"; кеш недоступен: {cache_error}"
            if upload_error:
                detail += f"; ручной файл отклонён: {upload_error}"
            return df, SourceStatus(name, "fallback", detail, path_timestamp(fallback_path))
        except Exception as exc:
            fallback_error = str(exc)

    if manual_path is not None and manual_path.exists():
        try:
            df = _read_offdays_payload(manual_path.read_bytes(), manual_path.name)
            _validate_offdays(df)
            detail = f"последний сохранённый ручной файл: {manual_path.name}; live-источник недоступен: {live_error}"
            if cache_error:
                detail += f"; кеш недоступен: {cache_error}"
            if fallback_error:
                detail += f"; fallback недоступен: {fallback_error}"
            return df, SourceStatus(name, "manual", detail, path_timestamp(manual_path))
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
