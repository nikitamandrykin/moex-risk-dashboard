from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import BinaryIO, Iterable
import os
import re
import tempfile
import json
import shutil
import subprocess

import pandas as pd
import requests


@dataclass(frozen=True)
class SourceStatus:
    name: str
    state: str  # live | upload | cache | fallback | manual | missing | error
    detail: str
    updated_at: str = ""


NUMERIC_COLUMNS = {
    "mr1", "mr2", "mr3", "lk1", "lk2",
    "autoshiftnummr", "autoshiftnummrevg", "autoshiftnumirevg",
    "futmontime", "futmontimeevg", "csmontimeevg", "rangefut",
    "offdaystradingpricerangeshift",
}

ALIASES = {
    "underlying": "assetcode",
    "кодба": "assetcode",
    "кодбазовогоактива": "assetcode",
    "bc": "assetcode",
    "basecontract": "assetcode",
    "base_contract": "assetcode",
    "basecontractcode": "assetcode",
    "base_contract_code": "assetcode",
    "auto_shift_num_mr": "autoshiftnummr",
    "autoshiftnummrday": "autoshiftnummr",
    "auto_shift_num_mr_evg": "autoshiftnummrevg",
    "autoshiftnummrevg": "autoshiftnummrevg",
    "auto_shift_num_ir_evg": "autoshiftnumirevg",
    "autoshiftnumirevg": "autoshiftnumirevg",
    "futmontimeday": "futmontime",
    "futmontimeevg": "futmontimeevg",
    "fut_mon_time_evg": "futmontimeevg",
    "csmontimeevg": "csmontimeevg",
    "cs_mon_time_evg": "csmontimeevg",
    "fut_mon_time_day": "futmontime",
    "futrange": "rangefut",
    "range_fut": "rangefut",
    "offdays_trading_price_range_shift": "offdaystradingpricerangeshift",
    "offdaystradingpricerangeshift": "offdaystradingpricerangeshift",
    "weekendcorridor": "offdaystradingpricerangeshift",
    "weekend_corridor": "offdaystradingpricerangeshift",
    "updated_at": "updatetime",
}


def _decode_bytes(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _compact_header(value: object) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", str(value or "").strip().casefold())


def _find_header_line(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        lower = line.lower()
        if "assetcode" in lower or ("mr1" in lower and "lk1" in lower):
            return i
    return 0


def _find_excel_header_row(raw: pd.DataFrame) -> int:
    for index, row in raw.iterrows():
        compact = {_compact_header(value) for value in row.tolist() if str(value or "").strip()}
        if "assetcode" in compact or ("mr1" in compact and "lk1" in compact):
            return int(index)
        if "кодба" in compact and ("mr1" in compact or "ставкариска1гоуровня" in compact):
            return int(index)
    return 0


def _normalise_columns(columns: Iterable[object]) -> list[str]:
    result: list[str] = []
    for col in columns:
        key = str(col).strip().lower().replace("\u00a0", " ")
        key = re.sub(r"\s+", "", key).replace("-", "_")
        result.append(ALIASES.get(key, key))
    return result


def _parse_number(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not text or text.lower() in {"nan", "none", "null", "-", "—"}:
        return None
    is_percent = "%" in text
    text = text.replace("%", "").replace(",", ".")
    try:
        number = float(text)
        return number / 100 if is_percent else number
    except ValueError:
        return None


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = _normalise_columns(df.columns)
    df = df.dropna(how="all")
    df = df.loc[:, ~pd.Index(df.columns).astype(str).str.startswith("unnamed")]

    if "assetcode" in df.columns:
        cleaned = (
            df["assetcode"]
            .astype(str)
            .str.replace("\u00a0", " ", regex=False)
            .str.replace("\u200b", "", regex=False)
            .str.strip()
        )
        df["assetcode"] = cleaned
        df = df[cleaned.ne("") & cleaned.str.casefold().ne("nan")]

    for col in NUMERIC_COLUMNS.intersection(df.columns):
        df[col] = df[col].map(_parse_number)

    for col in ("tradedate", "updatetime"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="mixed", dayfirst=True, errors="coerce")

    return df.reset_index(drop=True)


def read_ncc_csv(payload: bytes) -> pd.DataFrame:
    """Read MOEX/NCC CSV exports, including service rows before the header."""
    text = _decode_bytes(payload)
    lines = text.splitlines()
    header_index = _find_header_line(lines)
    body = "\n".join(lines[header_index:])
    if not body.strip():
        return pd.DataFrame()

    # MOEX/NCC exports normally use semicolons. Decimal commas must not be
    # mistaken for a comma-delimited CSV, so prefer ';' whenever present in the header.
    header = lines[header_index] if lines else ""
    delimiter = ";" if ";" in header else ","
    df = pd.read_csv(StringIO(body), sep=delimiter, dtype=str)
    return _clean_frame(df)


def read_ncc_xlsx(payload: bytes) -> pd.DataFrame:
    """Read a manual NCC/MOEX XLSX and locate the real header row automatically."""
    if not payload:
        return pd.DataFrame()
    sheets = pd.read_excel(BytesIO(payload), sheet_name=None, header=None, dtype=object, engine="openpyxl")
    candidates: list[pd.DataFrame] = []
    for raw in sheets.values():
        if raw is None or raw.empty:
            continue
        header_index = _find_excel_header_row(raw)
        header = raw.iloc[header_index].tolist()
        body = raw.iloc[header_index + 1 :].copy()
        body.columns = header
        cleaned = _clean_frame(body)
        if not cleaned.empty:
            candidates.append(cleaned)
    if not candidates:
        return pd.DataFrame()
    return pd.concat(candidates, ignore_index=True, sort=False)


def read_moex_iss_json(payload: bytes, block_name: str | None = None) -> pd.DataFrame:
    """Read the standard MOEX ISS JSON shape into a normalized DataFrame.

    ISS returns blocks as ``{name: {columns: [...], data: [...]}}``.  The
    function also tolerates the extended JSON representation used by some ISS
    endpoints.  Only data blocks containing tabular rows are considered.
    """
    if not payload:
        return pd.DataFrame()
    obj = json.loads(_decode_bytes(payload))

    candidates: list[tuple[str, object]] = []
    if isinstance(obj, dict):
        if block_name and block_name in obj:
            candidates.append((block_name, obj[block_name]))
        candidates.extend((str(k), v) for k, v in obj.items() if not block_name or k != block_name)
    elif isinstance(obj, list):
        # Extended ISS JSON can be a list of dictionaries.
        for item in obj:
            if isinstance(item, dict):
                candidates.extend((str(k), v) for k, v in item.items())

    for name, block in candidates:
        if isinstance(block, dict):
            columns = block.get("columns")
            data = block.get("data")
            if isinstance(columns, list) and isinstance(data, list):
                return _clean_frame(pd.DataFrame(data, columns=columns))
        # Extended shape: {name: [{metadata...}, [rows-as-dicts]]}
        if isinstance(block, list) and len(block) >= 2 and isinstance(block[1], list):
            rows = block[1]
            if rows and all(isinstance(row, dict) for row in rows):
                return _clean_frame(pd.DataFrame(rows))

    return pd.DataFrame()


def read_tabular_payload(payload: bytes, filename: str = "") -> pd.DataFrame:
    """Read MOEX/NCC CSV, JSON or XLSX payloads.

    Automatic ISS loading uses JSON first because it has an explicit schema and
    is less sensitive to CSV delimiter/content-negotiation differences.
    """
    suffix = Path(filename or "").suffix.lower()
    stripped = payload.lstrip()
    if suffix in {".xlsx", ".xlsm"} or payload[:2] == b"PK":
        return read_ncc_xlsx(payload)
    if suffix == ".json" or stripped[:1] in {b"{", b"["}:
        return read_moex_iss_json(payload)
    return read_ncc_csv(payload)


def _read_upload(uploaded_file: BinaryIO | None) -> tuple[bytes | None, str]:
    if uploaded_file is None:
        return None, ""
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    name = str(getattr(uploaded_file, "name", "") or "")
    return uploaded_file.read(), name


BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36 "
    "MOEX-Risk-Dashboard/0.9"
)


def _curl_fetch(
    url: str,
    *,
    timeout: int,
    accept: str = "*/*",
    referer: str = "",
) -> bytes:
    """Use the OS curl client as a verified-HTTPS fallback.

    On current Windows builds curl uses the Windows TLS/certificate stack in
    many installations, which is useful when Python/requests cannot validate a
    corporate or locally-intercepted certificate although the browser can.
    Certificate verification is NOT disabled.
    """
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise RuntimeError("системный curl не найден")

    command = [
        curl,
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--connect-timeout", str(max(5, min(12, timeout))),
        "--max-time", str(timeout),
        "--user-agent", BROWSER_USER_AGENT,
        "--header", f"Accept: {accept}",
    ]
    if referer:
        command += ["--referer", referer]
    command.append(url)

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout + 5,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"curl завершился с кодом {completed.returncode}")
    if not completed.stdout:
        raise ValueError("системный curl вернул пустой ответ")
    return completed.stdout


def fetch_url(
    url: str,
    timeout: int = 25,
    attempts: int = 3,
    *,
    accept: str = "text/csv,application/csv,text/plain,application/octet-stream,*/*",
    referer: str = "",
) -> bytes:
    """Fetch an official source without tying availability to one TLS client.

    Order: requests with normal environment/proxy settings -> requests without
    environment proxy variables -> system curl. All transports keep HTTPS
    certificate verification enabled.
    """
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": accept,
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer

    errors: list[str] = []

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            if not response.content:
                raise ValueError("источник вернул пустой ответ")
            return response.content
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                import time
                time.sleep(0.45 * (attempt + 1))
    if last_error is not None:
        errors.append(f"requests: {type(last_error).__name__}: {last_error}")

    # A broken HTTPS_PROXY/REQUESTS_CA_BUNDLE environment can affect requests
    # while a direct connection still works. Try once without inherited proxy
    # settings before moving to the OS client.
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        if not response.content:
            raise ValueError("источник вернул пустой ответ")
        return response.content
    except Exception as exc:
        errors.append(f"requests-direct: {type(exc).__name__}: {exc}")

    try:
        return _curl_fetch(url, timeout=timeout, accept=accept, referer=referer)
    except Exception as exc:
        errors.append(f"curl: {type(exc).__name__}: {exc}")

    raise RuntimeError(" | ".join(errors))


def _validate_dataframe(
    df: pd.DataFrame,
    required_columns: set[str] | None,
) -> None:
    if df.empty:
        raise ValueError("источник не содержит строк данных")
    if required_columns:
        missing = sorted(required_columns.difference(df.columns))
        if missing:
            raise ValueError("в ответе нет колонок: " + ", ".join(missing))
        for column in sorted(required_columns):
            series = df[column]
            if column == "assetcode":
                usable = series.astype(str).str.strip().replace("nan", "").ne("").any()
            else:
                usable = series.notna().any()
            if not usable:
                raise ValueError(f"колонка {column} не содержит пригодных значений")


def _read_and_validate(
    payload: bytes,
    required_columns: set[str] | None,
    *,
    filename: str = "",
) -> pd.DataFrame:
    df = read_tabular_payload(payload, filename=filename)
    _validate_dataframe(df, required_columns)
    return df


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        temp_name = tmp.name
    Path(temp_name).replace(path)


def _atomic_write_frame(path: Path, df: pd.DataFrame) -> None:
    payload = df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    _atomic_write_bytes(path, payload)


def path_timestamp(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime("%d.%m.%Y %H:%M:%S")
    except OSError:
        return ""


def dataframe_timestamp(df: pd.DataFrame) -> str:
    """Best data timestamp visible in a normalized dataset."""
    if df is None or df.empty:
        return ""
    for column in ("systime", "updatetime", "tradedate", "trade_session_date"):
        if column not in df.columns:
            continue
        values = pd.to_datetime(df[column], errors="coerce")
        if values.notna().any():
            return pd.Timestamp(values.max()).strftime("%d.%m.%Y %H:%M:%S")
    return ""


def load_dataset(
    *,
    name: str,
    env_url_name: str,
    fallback_path: Path | None = None,
    cache_path: Path | None = None,
    manual_path: Path | None = None,
    required_columns: set[str] | None = None,
    uploaded_file: BinaryIO | None = None,
    default_url: str | None = None,
    alternate_urls: Iterable[str] | None = None,
) -> tuple[pd.DataFrame, SourceStatus]:
    """Load a critical tabular dataset without discarding the last known-good copy.

    Automatic order: live official URL -> last-good cache -> built-in fallback ->
    persisted manual copy. An explicit upload is validated first because it is an
    intentional user action; a bad upload never overwrites the persisted copy.
    """
    uploaded_payload, uploaded_name = _read_upload(uploaded_file)
    upload_error = ""
    if uploaded_payload:
        try:
            df = _read_and_validate(
                uploaded_payload,
                required_columns,
                filename=uploaded_name,
            )
            if manual_path is not None:
                try:
                    _atomic_write_frame(manual_path, df)
                except OSError:
                    pass
            return df, SourceStatus(
                name,
                "upload",
                f"ручной файл {uploaded_name or 'upload'}; проверка пройдена",
                dataframe_timestamp(df) or path_timestamp(manual_path),
            )
        except Exception as exc:
            upload_error = str(exc)

    configured_url = os.getenv(env_url_name, "").strip()
    candidates: list[str] = []
    for candidate in [configured_url, (default_url or "").strip(), *(alternate_urls or [])]:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    live_errors: list[str] = []
    for url in candidates:
        try:
            payload = fetch_url(url)
            df = _read_and_validate(payload, required_columns, filename=url)
            if cache_path is not None:
                try:
                    # Cache the normalized table, not the transport payload.  This
                    # keeps the cache readable even when the live source was JSON.
                    _atomic_write_frame(cache_path, df)
                except OSError:
                    pass
            detail = url
            if len(candidates) > 1 and url != candidates[0]:
                detail += "; использован резервный официальный формат/URL"
            if upload_error:
                detail += f"; ручной файл отклонён: {upload_error}"
            return df, SourceStatus(name, "live", detail, dataframe_timestamp(df))
        except Exception as exc:
            live_errors.append(f"{url}: {exc}")

    live_error = " | ".join(live_errors)

    cache_error = ""
    if cache_path is not None and cache_path.exists():
        try:
            df = _read_and_validate(cache_path.read_bytes(), required_columns, filename=cache_path.name)
            detail = f"последний успешный снимок: {cache_path.name}"
            if live_error:
                detail += f"; live-источник недоступен: {live_error}"
            if upload_error:
                detail += f"; ручной файл отклонён: {upload_error}"
            return df, SourceStatus(
                name,
                "cache",
                detail,
                dataframe_timestamp(df) or path_timestamp(cache_path),
            )
        except Exception as exc:
            cache_error = str(exc)

    fallback_error = ""
    if fallback_path and fallback_path.exists():
        try:
            df = _read_and_validate(fallback_path.read_bytes(), required_columns, filename=fallback_path.name)
            detail = f"встроенный локальный снимок: {fallback_path.name}"
            if live_error:
                detail += f"; live-источник недоступен: {live_error}"
            if cache_error:
                detail += f"; локальный кеш повреждён: {cache_error}"
            if upload_error:
                detail += f"; ручной файл отклонён: {upload_error}"
            return df, SourceStatus(
                name,
                "fallback",
                detail,
                dataframe_timestamp(df) or path_timestamp(fallback_path),
            )
        except Exception as exc:
            fallback_error = str(exc)

    if manual_path is not None and manual_path.exists():
        try:
            df = _read_and_validate(manual_path.read_bytes(), required_columns, filename=manual_path.name)
            detail = f"последний сохранённый ручной файл: {manual_path.name}"
            if live_error:
                detail += f"; live-источник недоступен: {live_error}"
            if cache_error:
                detail += f"; кеш недоступен: {cache_error}"
            if fallback_error:
                detail += f"; fallback недоступен: {fallback_error}"
            if upload_error:
                detail += f"; новый ручной файл отклонён: {upload_error}"
            return df, SourceStatus(
                name,
                "manual",
                detail,
                dataframe_timestamp(df) or path_timestamp(manual_path),
            )
        except Exception as exc:
            manual_error = str(exc)
    else:
        manual_error = ""

    errors = []
    if upload_error:
        errors.append(f"upload: {upload_error}")
    if live_error:
        errors.append(f"live: {live_error}")
    if cache_error:
        errors.append(f"cache: {cache_error}")
    if fallback_error:
        errors.append(f"fallback: {fallback_error}")
    if manual_error:
        errors.append(f"manual: {manual_error}")
    if errors:
        return pd.DataFrame(), SourceStatus(name, "error", "; ".join(errors))
    return pd.DataFrame(), SourceStatus(name, "missing", f"не задан {env_url_name} и нет локального файла")


def _assetcode_key(value: object) -> str:
    return str(value or "").replace("\u00a0", " ").replace("\u200b", "").strip().upper()


def latest_row(df: pd.DataFrame, assetcode: str) -> pd.Series | None:
    if df.empty or "assetcode" not in df.columns:
        return None
    target = _assetcode_key(assetcode)
    keys = df["assetcode"].map(_assetcode_key)
    rows = df[keys == target].copy()
    if rows.empty:
        return None
    sort_cols = [col for col in ("updatetime", "tradedate") if col in rows.columns]
    if sort_cols:
        rows = rows.sort_values(sort_cols, na_position="first")
    return rows.iloc[-1]


def union_assetcodes(*frames: pd.DataFrame) -> list[str]:
    values: dict[str, str] = {}
    for frame in frames:
        if not frame.empty and "assetcode" in frame.columns:
            for value in frame["assetcode"].dropna().astype(str):
                display = value.replace("\u00a0", " ").replace("\u200b", "").strip()
                key = _assetcode_key(display)
                if key and key != "NAN":
                    values.setdefault(key, display)
    return sorted(values.values(), key=lambda x: x.upper())


def newest_timestamp(*rows: pd.Series | None) -> pd.Timestamp | None:
    timestamps: list[pd.Timestamp] = []
    for row in rows:
        if row is None:
            continue
        for col in ("updatetime", "tradedate"):
            value = row.get(col)
            if value is not None and not pd.isna(value):
                timestamps.append(pd.Timestamp(value))
                break
    return max(timestamps) if timestamps else None
