from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qs, urljoin, urlparse
import base64
import os
import re

import pandas as pd

from .loaders import SourceStatus, load_dataset


CATALOG_URL = "https://www.nationalclearingcentre.ru/catalog/030902"
LINK_TEXT_MARKER = "Статические параметры, применяемые в вечернюю сессию"
DEFAULT_XLSX_URL = (
    "https://www.nationalclearingcentre.ru/connector?cmd=file&"
    "target=A_XNCa0YDQsNGB0LDQstC40L1cZGVyaXZhdGl2ZXNTdGF0aWNQYXJhbXMtMDNfMDhfMjAyNi54bHN4&"
    "_t=1785751902"
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


def _discover_from_catalog(timeout: int = 30) -> str:
    # Import here so resilience tests can monkeypatch the common HTTP layer.
    from .offdays import _http_get

    response = _http_get(CATALOG_URL, timeout=timeout, accept="text/html,*/*")
    parser = _LinkCollector()
    parser.feed(response.text)
    for href, text in parser.links:
        if LINK_TEXT_MARKER.casefold() in text.casefold():
            return urljoin(CATALOG_URL, href)
    raise ValueError("На странице НКЦ не найдена ссылка на вечерние статические параметры")


def discover_evening_xlsx_url(timeout: int = 30) -> str:
    override = os.getenv("NCC_EVENING_STATIC_PARAMS_XLSX_URL", "").strip()
    if not override:
        # Backward compatibility with the previous MVP variable.
        override = os.getenv("NCC_EXTRA_PARAMS_CSV_URL", "").strip()
    if override:
        return override
    try:
        return _discover_from_catalog(timeout=timeout)
    except Exception:
        # The current official connector URL is a useful bootstrap fallback.
        # The app still falls back to last-good cache / bundled official snapshot
        # if this direct link later becomes unavailable.
        return DEFAULT_XLSX_URL


def source_file_label(url: str) -> str:
    """Best-effort human-readable connector filename for status text."""
    try:
        target = parse_qs(urlparse(url).query).get("target", [""])[0]
        if target:
            encoded = target[1:] if len(target) > 1 else target
            decoded = base64.b64decode(encoded + "===").decode("utf-8", errors="ignore")
            filename = decoded.replace("\\", "/").split("/")[-1].strip()
            if filename:
                return filename
    except Exception:
        pass
    return Path(urlparse(url).path).name or "официальный XLSX НКЦ"


def source_date_label(url: str) -> str:
    filename = source_file_label(url)
    match = re.search(r"(\d{2})_(\d{2})_(\d{4})", filename)
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{day}.{month}.{year}"


def load_evening_dataset(
    *,
    fallback_path: Path | None = None,
    cache_path: Path | None = None,
    manual_path: Path | None = None,
    uploaded_file: BinaryIO | None = None,
    timeout: int = 30,
) -> tuple[pd.DataFrame, SourceStatus]:
    url = discover_evening_xlsx_url(timeout=timeout)
    df, status = load_dataset(
        name="Вечерние параметры",
        env_url_name="NCC_EVENING_STATIC_PARAMS_XLSX_URL",
        default_url=url,
        fallback_path=fallback_path,
        cache_path=cache_path,
        manual_path=manual_path,
        uploaded_file=uploaded_file,
        required_columns={"assetcode", "autoshiftnummrevg", "futmontimeevg"},
    )

    if status.state == "live":
        filename = source_file_label(url)
        date_label = source_date_label(url)
        detail = f"официальный XLSX НКЦ: {filename}; {status.detail}"
        status = SourceStatus(status.name, status.state, detail, date_label or status.updated_at)
    elif status.state == "fallback" and fallback_path is not None:
        detail = f"встроенная официальная копия НКЦ: {fallback_path.name}; {status.detail}"
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", fallback_path.name)
        date_label = f"{match.group(3)}.{match.group(2)}.{match.group(1)}" if match else status.updated_at
        status = SourceStatus(status.name, status.state, detail, date_label)
    return df, status
