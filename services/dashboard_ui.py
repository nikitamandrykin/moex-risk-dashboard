"""Presentation helpers without network or Streamlit side effects."""
import hashlib
import html
import json
import math

import pandas as pd

from .loaders import _assetcode_key, latest_row
from .data_freshness import fetched_at_label, snapshot_expired


def asset_labels_for_codes(assetcodes, market_df):
    """Build human-readable underlying labels with one market table grouping.

    Preserve the original latest_row sort/selection semantics while avoiding
    a full scan of risk-rate rows for every underlying on each Streamlit rerun.
    """
    grouped = {}
    if market_df is not None and not market_df.empty and "assetcode" in market_df.columns:
        keys = market_df["assetcode"].map(_assetcode_key)
        grouped = {
            str(key): frame for key, frame in market_df.groupby(keys, sort=False)
        }
    labels = {}
    for code in assetcodes:
        matching = grouped.get(_assetcode_key(code))
        row = latest_row(matching, code) if matching is not None else None
        title = None if row is None else row.get("title")
        if title is not None and not pd.isna(title) and str(title).strip():
            labels[code] = f"{code} — {str(title).strip()}"
        else:
            labels[code] = code
    return labels


def selection_key(frame, fetched_at, threshold):
    # A row number is meaningful only for this ordered set and snapshot.
    payload = json.dumps([frame["secid"].tolist(), fetched_at, threshold], default=str)
    return "market_monitor_table_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def source_health_html(statuses, fetched_at, now):
    expired = snapshot_expired(fetched_at, now)
    labels = {"live": "Ответ API", "cache": "Последний снимок", "fallback": "Резервный набор",
              "manual": "Ручной файл", "upload": "Загружен файл", "missing": "Нет данных", "error": "Ошибка"}
    cards = []
    for status in statuses:
        tone = "bad" if status.state in {"missing", "error"} else ("ok" if status.state == "live" and not expired else "warn")
        label = labels.get(status.state, "Неизвестный статус")
        cards.append(f'<div class="source-health {tone}"><strong>{html.escape(status.name)}</strong>'
                     f'<span>{label}</span><small>{html.escape(str(status.updated_at or "Дата источника неизвестна"))}</small></div>')
    age = "Снимок требует обновления" if expired else "Снимок в пределах TTL 5 минут"
    return '<div class="source-grid">' + ''.join(cards) + '</div>' + (
        f'<div class="source-note">{age} · получен {fetched_at_label(fetched_at)}. '
        'Ответ API не гарантирует свежесть последней сделки.</div>')



def monitor_corridor_html(selected):
    """Compact, readable price position without implying a current quotation.

    Values are exclusively taken from the selected ISS snapshot. Visual
    position is clamped to the published corridor; out-of-range stays explicit.
    """
    def text(value):
        return html.escape(str(value if value is not None else "—"), quote=True)

    def finite(value):
        try:
            number = float(value)
        except (ValueError, TypeError):
            return None
        return number if math.isfinite(number) else None

    def amount(value):
        value = finite(value)
        if value is None:
            return "—"
        return f"{value:,.4f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")

    low = finite(selected.get("lowlimit"))
    high = finite(selected.get("highlimit"))
    price = finite(selected.get("price"))
    valid = low is not None and high is not None and price is not None and high > low
    code = text(selected.get("assetcode"))
    contract = text(selected.get("secid"))
    risk_status = str(selected.get("risk_status") or "NO DATA")
    label, tone = {
        "OUTSIDE LOW": ("Ниже LOW", "danger"),
        "OUTSIDE HIGH": ("Выше HIGH", "danger"),
        "CRITICAL": ("CRITICAL", "danger"),
        "WATCH": ("WATCH", "warning"),
        "NORMAL": ("NORMAL", "normal"),
        "NO DATA": ("Нет данных", "muted"),
    }.get(risk_status, ("Нет данных", "muted"))
    if not valid:
        label, tone = "Нет данных", "muted"
    source = {
        "last": "LAST", "settleprice": "SETTLEPRICE",
        "lastsettleprice": "LASTSETTLEPRICE",
        "prevsettleprice": "PREVSETTLEPRICE",
    }.get(str(selected.get("price_source") or "").lower(), "Не указан")
    source = text(source)
    pct = finite(selected.get("nearest_pct")) if valid else None
    nearest = "—" if pct is None else f"{pct:.2f}".replace(".", ",") + "%"
    if valid:
        position = max(0.0, min(100.0, (price - low) / (high - low) * 100))
        marker = (f'<span class="monitor-corridor-marker" style="left:{position:.2f}%"></span>')
        hint = "Ниже нижней границы" if price < low else ("Выше верхней границы" if price > high else "Внутри ценового коридора")
    else:
        marker = ""
        hint = "Недостаточно данных для оценки положения цены"
    date_value = selected.get("tradedate")
    trade_date = "—"
    if date_value is not None and not pd.isna(date_value):
        parsed = pd.to_datetime(date_value, errors="coerce")
        if not pd.isna(parsed):
            trade_date = parsed.strftime("%d.%m.%Y")

    return (
        f'<section class="monitor-corridor" aria-label="Положение цены {contract}">'
        f'<div class="monitor-corridor-heading">'
        f'<div><div class="monitor-corridor-eyebrow">Выбранный контракт · {code}</div>'
        f'<div class="monitor-corridor-name">{contract}</div></div>'
        f'<span class="monitor-corridor-badge {tone}">{label}</span></div>'
        f'<div class="monitor-corridor-values">'
        f'<div><span>LOWLIMIT</span><strong>{amount(low)}</strong></div>'
        f'<div><span>Цена ISS</span><strong>{amount(price)}</strong></div>'
        f'<div><span>HIGHLIMIT</span><strong>{amount(high)}</strong></div></div>'
        f'<div class="monitor-corridor-track" role="img" '
        f'aria-label="{text(hint)}: цена {amount(price)}, диапазон от {amount(low)} до {amount(high)}">'
        f'{marker}</div>'
        f'<div class="monitor-corridor-footer"><strong>{nearest} до ближайшей границы</strong>'
        f'<span>{text(hint)}</span></div>'
        f'<div class="monitor-corridor-footnote">Цена: {source} · TRADEDATE: {text(trade_date)}. '
        f'Отметка торговой даты не гарантирует время последней сделки.</div>'
        f'</section>'
    )



def parameter_reference_html(records):
    """Adaptive parameter reference: no fixed-width table or clipped columns."""
    cards = []
    for row in records:
        def safe(field):
            value = row.get(field, "")
            if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
                return ""
            return html.escape(str(value), quote=True)
        code = safe("parameter")
        title = safe("title")
        unit = safe("unit")
        description = safe("short_description")
        usage = safe("how_to_read")
        cards.append(
            '<article class="parameter-reference-item">'
            f'<div class="parameter-reference-head"><code>{code}</code>'
            f'<span class="parameter-reference-unit">{unit}</span></div>'
            f'<h3>{title}</h3><p>{description}</p>'
            f'<details><summary>Как применять</summary><div>{usage}</div></details>'
            '</article>'
        )
    return '<div class="parameter-reference-grid">' + ''.join(cards) + '</div>'


def dashboard_styles():
    return """<style>
    .stApp { background:#fff; }
    .hero, .presentation-hero { background:#fff; box-shadow:none; border:0;
      border-bottom:2px solid #d92d3a; border-radius:0; padding:1rem 0; }
    .hero:after { display:none; }
    .hero h1, .presentation-hero h1 { font-size:1.65rem; letter-spacing:-.04em; }
    .metric-card, .kpi-card, .asset-panel { border-radius:8px; box-shadow:none; }
    .kpi-card { min-height:100px; padding:.85rem 1rem; background:#f7f8fa; }
    .kpi-value { font-size:1.8rem; font-variant-numeric:tabular-nums; }
    .kpi-meta, .section-subtitle { font-size:.8rem; line-height:1.5; }
    .section-title { font-size:1.12rem; }
    .section-head { margin:1.1rem 0 .6rem; }
    .source-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; }
    .source-health { border:1px solid #e6eaf0; border-top:3px solid #b54708;
      border-radius:6px; padding:10px; display:flex; flex-direction:column; gap:5px; min-width:0; }
    .source-health.ok { border-top-color:#16805d; }
    .source-health.bad { border-top-color:#d92d3a; }
    .source-health strong { font-size:.78rem; overflow-wrap:anywhere; }
    .source-health span { font-size:.76rem; }
    .source-health small, .source-note { color:#667085; font-size:.72rem; overflow-wrap:anywhere; }
    .source-note { margin:8px 0 16px; }
    [data-testid="stDataFrame"] { border:1px solid #e6eaf0; border-radius:8px; }
    .block-container { max-width:1500px; }
    .section-head { border-bottom:1px solid #edf0f4; padding-bottom:.6rem; }
    .section-title { color:#142033; font-weight:720; }
    .kpi-card { border:1px solid #e9edf2; transition:border-color .15s ease; }
    .kpi-card:hover { border-color:#cbd5e1; }
    .kpi-value, .parameter-value, .corridor-edge-value { font-variant-numeric:tabular-nums; }
    .monitor-corridor { border:1px solid #e4e7ec; border-radius:12px; padding:18px 20px;
      margin:14px 0 18px; background:linear-gradient(180deg,#fff,#fbfcfd); }
    .monitor-corridor-heading { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; }
    .monitor-corridor-eyebrow { font-size:.72rem; color:#667085; font-weight:600; }
    .monitor-corridor-name { font-weight:750; color:#172033; font-size:1.25rem; padding-top:4px; }
    .monitor-corridor-badge { font-size:.73rem; font-weight:750; letter-spacing:.01em;
      padding:6px 11px; border-radius:6px; white-space:nowrap; }
    .monitor-corridor-badge.danger { color:#b4232f; background:#fff1f3; }
    .monitor-corridor-badge.warning { color:#92400e; background:#fff5df; }
    .monitor-corridor-badge.normal { color:#05603a; background:#ecfdf3; }
    .monitor-corridor-badge.muted { color:#475467; background:#f2f4f7; }
    .monitor-corridor-values { display:grid; grid-template-columns:repeat(3,minmax(0,1fr));
      gap:12px; margin:16px 0 14px; }
    .monitor-corridor-values > div { display:flex; flex-direction:column; gap:3px; min-width:0; }
    .monitor-corridor-values > div:nth-child(2) { text-align:center; }
    .monitor-corridor-values > div:nth-child(3) { text-align:right; }
    .monitor-corridor-values span { color:#667085; font-size:.69rem; letter-spacing:.03em; }
    .monitor-corridor-values strong { font-size:1.1rem; color:#101828;
      font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
    .monitor-corridor-track { position:relative; height:10px; border-radius:999px;
      background:linear-gradient(90deg,#fde8e9 0%,#eef1f5 20%,#eef1f5 80%,#fde8e9 100%);
      border:1px solid #d9e0e8; margin:15px 5px 10px; }
    .monitor-corridor-marker { position:absolute; top:50%; height:20px; width:5px;
      border-radius:4px; background:#d92d3a; transform:translate(-50%,-50%);
      box-shadow:0 0 0 3px white,0 2px 8px #0002; }
    .monitor-corridor-footer { display:flex; justify-content:space-between; gap:12px;
      font-size:.82rem; color:#475467; }
    .monitor-corridor-footer strong { color:#172033; }
    .monitor-corridor-footnote { color:#667085; font-size:.71rem; margin-top:13px;
      border-top:1px solid #edf0f4; padding-top:9px; }
    @media(max-width:650px) { .monitor-corridor { padding:13px; }
      .monitor-corridor-values strong { font-size:.88rem; }
      .monitor-corridor-footer { flex-direction:column; gap:3px; }
      .monitor-corridor-heading { align-items:flex-start; }
    }
    @media(max-width:1000px) { .source-grid { grid-template-columns:repeat(3,minmax(0,1fr)); } }
    @media(max-width:600px) { .source-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .block-container { padding-left:1rem; padding-right:1rem; }
      .presentation-hero-tag { display:none; } }
    /* v2 usability: readable, scrollable page instead of clipped mini-tables. */
    .stApp { background:#f8f9fb !important; }
    [data-testid="stMainBlockContainer"], .block-container {
      width:100%; max-width:1500px !important; min-width:0;
      padding-left:clamp(1rem,3vw,2.5rem) !important;
      padding-right:clamp(1rem,3vw,2.5rem) !important;
      padding-bottom:3rem !important;
    }
    [data-testid="stHorizontalBlock"] > div { min-width:0; }
    .hero, .presentation-hero { margin-bottom:.6rem; }
    .selection-title { white-space:normal !important; overflow:visible !important;
      text-overflow:clip !important; line-height:1.4; }
    .source-summary {
      display:flex; flex-wrap:wrap; align-items:center; gap:.5rem 1rem;
      border:1px solid #e5e7eb; border-radius:8px; background:#fff;
      padding:.48rem .8rem; margin:.15rem 0 .5rem;
      color:#475467; font-size:.77rem;
    }
    .source-summary-status { font-weight:720; color:#067647; }
    .source-summary.source-fallback .source-summary-status { color:#92400e; }
    .source-summary.source-alert .source-summary-status { color:#b4232f; }
    .source-summary-meta { color:#475467; font-variant-numeric:tabular-nums; }
    [data-testid="stSegmentedControl"] { width:100%; max-width:100%; margin:.2rem 0 .7rem; }
    [data-testid="stSegmentedControl"] button { border-radius:8px !important;
      font-weight:650 !important; font-size:.86rem !important; }
    [data-testid="stDataFrame"] { width:100%; max-width:100%;
      border:1px solid #e3e7ee; border-radius:9px; }
    .parameter-reference-grid {
      display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
      gap:12px; margin:.65rem 0 1.5rem; width:100%;
    }
    .parameter-reference-item {
      min-width:0; border:1px solid #e4e8ef; border-radius:12px;
      background:#fff; padding:16px 18px;
    }
    .parameter-reference-head { display:flex; flex-wrap:wrap;
      justify-content:space-between; gap:6px 10px; align-items:center; }
    .parameter-reference-item code { background:#fef0f1; color:#b4232f;
      border-radius:5px; padding:3px 7px; font-size:.77rem;
      white-space:normal; overflow-wrap:anywhere; }
    .parameter-reference-unit { color:#667085; font-size:.73rem; }
    .parameter-reference-item h3 { color:#1d2939; font-size:1rem;
      margin:.6rem 0 .36rem; line-height:1.35; }
    .parameter-reference-item p { color:#344054; font-size:.83rem;
      line-height:1.5; margin:0 0 .65rem; }
    .parameter-reference-item details { border-top:1px solid #edf0f4;
      padding-top:.65rem; color:#344054; font-size:.82rem; line-height:1.5; }
    .parameter-reference-item summary { cursor:pointer; color:#b4232f;
      font-weight:650; }
    .parameter-reference-item details div { padding:.6rem 0 0; }
    @media (max-width:850px) {
      .parameter-reference-grid { grid-template-columns:1fr; }
    }
    @media (max-width:650px) {
      [data-testid="stMainBlockContainer"], .block-container {
        padding-left:.8rem !important; padding-right:.8rem !important;
      }
      .source-summary { padding:.55rem .65rem; }
      .parameter-reference-item { padding:13px 14px; }
      .kpi-card { min-height:75px; }
      .kpi-value { font-size:1.4rem !important; white-space:normal; }
      .section-head { margin-top:.8rem !important; }
    }
    </style>"""
