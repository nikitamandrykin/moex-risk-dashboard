from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
import html
import math
import os

import pandas as pd
import streamlit as st

from services.loaders import (
    SourceStatus,
    latest_row,
    load_dataset,
    newest_timestamp,
    union_assetcodes,
)
from services.contracts import (
    concentration_limit_to_rub,
    contract_value_rub,
    contracts_for_asset,
    load_forts_contracts,
    morning_reference_price,
    price_to_rub,
    reference_price,
    progressive_position_margin,
)
from services.formatters import (
    fmt_integer,
    fmt_number,
    fmt_rate,
    fmt_rub,
    fmt_compact_rub,
    is_missing,
)
from services.offdays import load_offdays_dataset
from services.evening import load_evening_dataset
from services.boundaries import (
    estimate_morning_limits,
    is_currency_future,
    is_morning_session,
)
from services.monitor import build_market_monitor, monitor_groups
from services.collateral import load_collateral_sources, lookup_collateral
from services.special_params import (
    PARAMETER_META,
    active_special_parameters,
    calendar_assetcodes,
    calendar_wide_view,
    future_special_periods,
    load_special_calendar_dataset,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PUBLIC_DEPLOYMENT = os.getenv("MOEX_PUBLIC_DEPLOYMENT", "0").strip().lower() in {"1", "true", "yes", "on"}

MARKET_RATES_JSON_URL = (
    "https://iss.moex.com/iss/rms/engines/futures/objects/limits.json"
    "?iss.meta=off&iss.only=limits"
)
MARKET_RATES_CSV_URL = (
    "https://iss.moex.com/iss/rms/engines/futures/objects/limits.csv"
    "?iss.meta=off&iss.only=limits"
)

STATIC_PARAMS_JSON_URL = (
    "https://iss.moex.com/iss/rms/engines/futures/objects/staticparams.json"
    "?iss.meta=off&iss.only=staticparams"
)
STATIC_PARAMS_CSV_URL = (
    "https://iss.moex.com/iss/rms/engines/futures/objects/staticparams.csv"
    "?iss.meta=off&iss.only=staticparams"
)

st.set_page_config(
    page_title="MOEX Risk Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
:root {
  --ink: #172033;
  --muted: #667085;
  --line: #E6EAF0;
  --surface: #FFFFFF;
  --surface-soft: #F7F8FA;
  --accent: #D92D3A;
  --accent-soft: #FFF1F3;
  --blue-soft: #EEF4FF;
  --green-soft: #ECFDF3;
  --amber-soft: #FFF8E7;
}

html, body, [class*="css"] { color: var(--ink); }
.block-container {
  padding-top: 1.35rem;
  padding-bottom: 3.5rem;
  max-width: 1440px;
}

.hero {
  position: relative;
  overflow: hidden;
  padding: 1.45rem 1.55rem;
  border: 1px solid var(--line);
  border-radius: 22px;
  background: linear-gradient(135deg, #FFFFFF 0%, #FAFBFD 68%, #FFF2F3 100%);
  box-shadow: 0 10px 28px rgba(16, 24, 40, .055);
  margin-bottom: 1rem;
}
.hero:after {
  content: "";
  position: absolute;
  width: 190px;
  height: 190px;
  right: -85px;
  top: -105px;
  border-radius: 999px;
  background: rgba(217,45,58,.09);
}
.hero-kicker {
  font-size: .74rem;
  font-weight: 750;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: .35rem;
}
.hero h1 { margin: 0; font-size: 1.9rem; line-height: 1.15; letter-spacing: -.025em; }
.hero p { margin: .45rem 0 0 0; color: var(--muted); max-width: 850px; }

.asset-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: .9rem 1rem;
  background: var(--surface);
  margin: .45rem 0 .9rem 0;
}
.asset-panel-title { font-size: .82rem; color: var(--muted); }
.asset-panel-value { font-size: 1.08rem; font-weight: 720; margin-top: .08rem; }
.asset-panel-time { font-size: .8rem; color: var(--muted); text-align: right; }

.section-head { margin: 1.25rem 0 .65rem 0; }
.section-kicker {
  color: var(--accent);
  font-size: .72rem;
  font-weight: 760;
  letter-spacing: .09em;
  text-transform: uppercase;
}
.section-title { font-size: 1.12rem; font-weight: 720; margin-top: .08rem; }
.section-subtitle { color: var(--muted); font-size: .84rem; margin-top: .1rem; }

.metric-card {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 1rem 1.05rem;
  min-height: 126px;
  background: var(--surface);
  box-shadow: 0 6px 18px rgba(16, 24, 40, .035);
}
.metric-card:before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: var(--accent);
}
.metric-card.blue:before { background: #3B82F6; }
.metric-card.green:before { background: #12B76A; }
.metric-card.amber:before { background: #F79009; }
.metric-card.neutral:before { background: #98A2B3; }
.metric-code {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: .18rem .48rem;
  background: var(--surface-soft);
  color: var(--muted);
  font-size: .69rem;
  font-weight: 700;
  letter-spacing: .025em;
  margin-bottom: .45rem;
}
.metric-label { font-size: .88rem; color: var(--muted); min-height: 2.35rem; }
.metric-value {
  font-size: 2.08rem;
  line-height: 1.12;
  font-weight: 760;
  letter-spacing: -.035em;
  margin: .22rem 0 0 0;
  color: var(--ink);
}
.metric-card.compact .metric-value { font-size: 1.35rem; line-height: 1.32; letter-spacing: -.018em; }

.status-row { display: flex; gap: .45rem; flex-wrap: wrap; margin: .45rem 0 1rem 0; }
.status-chip {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: .28rem .62rem;
  font-size: .74rem;
  color: #475467;
  background: #FFFFFF;
}
.status-live { background: var(--green-soft); border-color: #ABEFC6; }
.status-upload { background: var(--blue-soft); border-color: #C7D7FE; }
.status-cache, .status-fallback, .status-manual { background: var(--amber-soft); border-color: #FEDF89; }
.status-missing, .status-error { background: var(--accent-soft); border-color: #FECDD3; }

.small-muted { color: var(--muted); font-size: .84rem; }
[data-testid="stMetricValue"] { font-size: 1.8rem; }
[data-testid="stTabs"] button { font-weight: 650; }
[data-testid="stExpander"] { border-color: var(--line); border-radius: 16px; overflow: hidden; }

.methodology-table-wrap {
  width: 100%;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #FFFFFF;
}
.methodology-table {
  width: 100%;
  min-width: 1650px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: .86rem;
  line-height: 1.42;
}
.methodology-table th,
.methodology-table td {
  padding: .72rem .78rem;
  border-bottom: 1px solid var(--line);
  border-right: 1px solid var(--line);
  vertical-align: top;
  white-space: normal;
  overflow-wrap: anywhere;
}
.methodology-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-soft);
  color: var(--muted);
  font-weight: 650;
  text-align: left;
}
.methodology-table th:nth-child(1), .methodology-table td:nth-child(1) { width: 12%; }
.methodology-table th:nth-child(2), .methodology-table td:nth-child(2) { width: 19%; }
.methodology-table th:nth-child(3), .methodology-table td:nth-child(3) { width: 14%; }
.methodology-table th:nth-child(4), .methodology-table td:nth-child(4) { width: 28%; }
.methodology-table th:nth-child(5), .methodology-table td:nth-child(5) { width: 27%; border-right: 0; }
.methodology-table tr:last-child td { border-bottom: 0; }


/* Presentation redesign */
body, .stApp { background: #F6F7F9; }
.block-container { max-width: 1480px; padding-top: 1.05rem; }

.hero {
  padding: 1.35rem 1.55rem;
  border-radius: 24px;
  border: 1px solid #E4E7EC;
  background:
    radial-gradient(circle at 92% 0%, rgba(201, 40, 54, .10), transparent 27%),
    linear-gradient(135deg, #FFFFFF 0%, #FBFCFE 100%);
  box-shadow: 0 12px 32px rgba(16, 24, 40, .045);
}
.hero-kicker { color: #B4232F; letter-spacing: .13em; }
.hero h1 { font-size: 2.15rem; font-weight: 780; color: #101828; }
.hero p { max-width: 920px; font-size: .92rem; }

[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: .25rem;
  background: #FFFFFF;
  border: 1px solid #E4E7EC;
  border-radius: 14px;
  padding: .28rem;
  box-shadow: 0 4px 12px rgba(16, 24, 40, .025);
}
[data-testid="stTabs"] button {
  border-radius: 10px;
  padding-left: 1rem;
  padding-right: 1rem;
  font-size: .9rem;
}
[data-testid="stTabs"] button[aria-selected="true"] {
  background: #F2F4F7;
  color: #101828;
}

[data-testid="stSelectbox"] label, [data-testid="stNumberInput"] label,
[data-testid="stDateInput"] label, [data-testid="stTimeInput"] label {
  font-weight: 650;
  color: #344054;
}

.section-head { margin: 1.45rem 0 .72rem 0; }
.section-title { font-size: 1.2rem; font-weight: 760; color: #101828; }
.section-subtitle { max-width: 980px; }

.metric-card {
  border-radius: 16px;
  min-height: 118px;
  box-shadow: none;
  transition: transform .14s ease, box-shadow .14s ease;
}
.metric-card:hover { transform: translateY(-1px); box-shadow: 0 8px 24px rgba(16,24,40,.055); }
.metric-note { color: #667085; font-size: .74rem; margin-top: .52rem; line-height: 1.35; }

.kpi-card {
  background: #FFFFFF;
  border: 1px solid #E4E7EC;
  border-radius: 18px;
  padding: .95rem 1rem .9rem;
  min-height: 132px;
  box-shadow: 0 5px 16px rgba(16,24,40,.028);
}
.kpi-card.risk { border-top: 3px solid #C92836; }
.kpi-card.limit { border-top: 3px solid #F79009; }
.kpi-card.market { border-top: 3px solid #344054; }
.kpi-label { color: #667085; font-size: .76rem; font-weight: 650; }
.kpi-value { color: #101828; font-size: 1.72rem; font-weight: 790; letter-spacing: -.035em; margin-top: .34rem; white-space: nowrap; }
.kpi-meta { color: #98A2B3; font-size: .7rem; margin-top: .42rem; line-height: 1.25; }

.summary-strip {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  border: 1px solid #E4E7EC; border-radius: 16px; background: #FFFFFF;
  padding: .78rem 1rem; margin: .55rem 0 .9rem;
}
.summary-left { display: flex; align-items: center; gap: .65rem; flex-wrap: wrap; }
.summary-dot { width: 9px; height: 9px; border-radius: 50%; background: #12B76A; display: inline-block; }
.summary-dot.warn { background: #F79009; }
.summary-dot.error { background: #D92D3A; }
.summary-title { font-weight: 720; font-size: .86rem; color: #344054; }
.summary-meta { font-size: .76rem; color: #667085; }

.mode-banner {
  border: 1px solid #E4E7EC; border-radius: 16px; background: #FFFFFF;
  padding: .78rem .95rem; display:flex; align-items:center; justify-content:space-between; gap:1rem;
  margin: .5rem 0 .85rem;
}
.mode-banner.special { background: #FFF8E7; border-color: #FEDF89; }
.mode-badge { display:inline-flex; border-radius:999px; padding:.24rem .58rem; font-size:.68rem; font-weight:780; letter-spacing:.06em; }
.mode-badge.normal { background:#ECFDF3; color:#027A48; }
.mode-badge.special { background:#FEF0C7; color:#B54708; }
.mode-title { font-size:.88rem; font-weight:720; color:#344054; }
.mode-desc { font-size:.74rem; color:#667085; margin-top:.12rem; }

.concentration-wrap {
  border: 1px solid #E4E7EC; background: #FFFFFF; border-radius: 18px;
  padding: 1.05rem 1.1rem 1rem; margin-top: .72rem;
}
.concentration-axis { display:grid; grid-template-columns: 1fr 1.2fr .8fr; gap:4px; height:15px; margin:.72rem 0 .38rem; }
.concentration-axis > div { border-radius:999px; }
.mr1-seg { background:#A6F4C5; }
.mr2-seg { background:#FEDF89; }
.mr3-seg { background:#FECDD3; }
.concentration-labels { display:grid; grid-template-columns:1fr 1.2fr .8fr; gap:.75rem; }
.concentration-label { font-size:.77rem; color:#475467; }
.concentration-label strong { display:block; color:#101828; font-size:.86rem; }
.concentration-thresholds { display:flex; justify-content:space-between; color:#667085; font-size:.72rem; margin-top:.72rem; border-top:1px dashed #D0D5DD; padding-top:.55rem; }

.corridor-card {
  border: 1px solid #E4E7EC; background:#FFFFFF; border-radius:18px;
  padding:1.05rem 1.15rem; margin:.4rem 0 .8rem;
}
.corridor-top { display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; }
.corridor-edge-label { font-size:.72rem; color:#667085; }
.corridor-edge-value { font-size:1.15rem; color:#101828; font-weight:760; }
.corridor-track { position:relative; height:10px; border-radius:999px; background:linear-gradient(90deg,#FEE4E2 0%,#F2F4F7 50%,#FEE4E2 100%); margin:1.35rem .15rem .65rem; }
.corridor-marker { position:absolute; top:50%; width:17px; height:17px; border-radius:50%; background:#344054; border:3px solid #FFFFFF; box-shadow:0 0 0 1px #344054; transform:translate(-50%,-50%); }
.corridor-marker:before { content:""; position:absolute; left:50%; bottom:18px; width:1px; height:16px; background:#98A2B3; }
.corridor-caption { display:flex; justify-content:space-between; gap:1rem; font-size:.72rem; color:#667085; }

.calc-hero { border:1px solid #E4E7EC; border-radius:20px; background:#FFFFFF; padding:1.05rem 1.1rem; margin:.4rem 0 .8rem; }
.position-bar { display:flex; height:18px; width:100%; border-radius:999px; overflow:hidden; background:#EAECF0; margin:.7rem 0 .55rem; }
.position-bar > div { min-width:0; }
.position-mr1 { background:#75E0A7; }
.position-mr2 { background:#FEC84B; }
.position-mr3 { background:#FDA29B; }
.position-legend { display:flex; flex-wrap:wrap; gap:.5rem 1.1rem; color:#475467; font-size:.74rem; }
.legend-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:.32rem; }
.legend-mr1 { background:#32D583; } .legend-mr2 { background:#F79009; } .legend-mr3 { background:#F04438; }

.estimate-box { border:1px solid #FEDF89; background:#FFFAEB; border-radius:15px; padding:.85rem .95rem; margin:.65rem 0; }
.estimate-badge { display:inline-flex; background:#FEF0C7; color:#B54708; font-size:.65rem; font-weight:780; border-radius:999px; padding:.2rem .5rem; letter-spacing:.05em; }
.estimate-title { font-weight:720; color:#344054; margin-top:.38rem; }
.estimate-text { color:#667085; font-size:.76rem; margin-top:.2rem; }


/* v0.9.3 presentation polish */
.block-container { max-width: 1540px; padding-top: .65rem; padding-bottom: 2.4rem; }
.hero { padding: .82rem 1.15rem; border-radius: 18px; margin-bottom: .55rem; }
.hero:after { width: 150px; height: 150px; right: -68px; top: -88px; }
.hero-kicker { font-size: .66rem; margin-bottom: .18rem; }
.hero h1 { font-size: 1.72rem; }
.hero p { margin-top: .24rem; font-size: .78rem; }
.section-head { margin: 1rem 0 .48rem; }
.section-title { font-size: 1.08rem; }
.section-subtitle { font-size: .78rem; }
.kpi-card { min-height: 102px; padding: .72rem .82rem .66rem; border-radius: 15px; }
.kpi-value { font-size: 1.48rem; margin-top: .24rem; }
.kpi-meta { margin-top: .3rem; }
.metric-card { min-height: 102px; padding: .75rem .82rem; border-radius: 15px; }
.metric-card.compact .metric-value { font-size: 1.22rem; }
.metric-label { min-height: auto; font-size: .8rem; }
.metric-code { margin-bottom: .32rem; font-size: .62rem; }
.metric-note { margin-top: .35rem; font-size: .68rem; }
.asset-panel { padding: .66rem .82rem; border-radius: 14px; margin: .35rem 0 .55rem; }
.asset-panel-value { font-size: .96rem; }
.asset-panel-title, .asset-panel-time { font-size: .72rem; }
.mode-badge { font-size: .6rem; padding: .18rem .46rem; }
.summary-strip { padding: .58rem .75rem; margin: .35rem 0 .55rem; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { border-radius: 12px; padding: .2rem; }
[data-testid="stTabs"] button { font-size: .82rem; padding-left: .8rem; padding-right: .8rem; }

.parameter-strip {
  display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:0;
  background:#FFFFFF; border:1px solid #E4E7EC; border-radius:16px; overflow:hidden;
}
.parameter-item { padding:.72rem .8rem; border-right:1px solid #EAECF0; min-width:0; }
.parameter-item:last-child { border-right:0; }
.parameter-code { color:#98A2B3; font-size:.61rem; font-weight:760; letter-spacing:.04em; text-transform:uppercase; }
.parameter-label { color:#667085; font-size:.72rem; margin-top:.18rem; }
.parameter-value { color:#101828; font-size:1.05rem; font-weight:760; margin-top:.18rem; white-space:nowrap; }
.parameter-note { color:#98A2B3; font-size:.63rem; margin-top:.18rem; }

.selection-compact {
  display:flex; justify-content:space-between; align-items:center; gap:1rem;
  background:#FFFFFF; border:1px solid #E4E7EC; border-radius:14px;
  padding:.58rem .75rem; margin:.28rem 0 .48rem;
}
.selection-main { min-width:0; }
.selection-title { font-weight:750; font-size:.9rem; color:#101828; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.selection-sub { color:#667085; font-size:.7rem; margin-top:.12rem; }
.selection-side { display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; justify-content:flex-end; }
.selection-time { color:#667085; font-size:.68rem; text-align:right; }

.concentration-wrap { padding:.82rem .9rem .72rem; border-radius:16px; margin-top:.45rem; }
.concentration-axis { height:12px; margin:.55rem 0 .3rem; }
.concentration-labels { gap:.45rem; }
.concentration-label { font-size:.7rem; }
.concentration-label strong { font-size:.78rem; }
.concentration-thresholds { font-size:.66rem; margin-top:.48rem; padding-top:.42rem; }
.scale-note { color:#98A2B3; font-size:.62rem; margin-top:.4rem; }

.corridor-card { padding:.82rem .9rem; border-radius:16px; }
.corridor-edge-value { font-size:1.05rem; }
.corridor-track { margin:1.05rem .1rem .52rem; }
.corridor-insights { display:grid; grid-template-columns:1fr 1fr 1fr; gap:.5rem; margin-top:.65rem; }
.corridor-insight { background:#F9FAFB; border-radius:10px; padding:.45rem .55rem; text-align:center; }
.corridor-insight-label { color:#98A2B3; font-size:.61rem; }
.corridor-insight-value { color:#344054; font-size:.75rem; font-weight:700; margin-top:.12rem; }

.quick-actions { display:flex; flex-wrap:wrap; gap:.35rem; margin:.25rem 0 .45rem; }
[data-testid="stButton"] button { border-radius:10px; }

@media (max-width: 1100px) {
  .parameter-strip { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .parameter-item { border-bottom:1px solid #EAECF0; }
  .selection-compact { align-items:flex-start; flex-direction:column; }
  .selection-side { justify-content:flex-start; }
}



/* v0.9.4 presentation mode */
.presentation-hero {
  padding: .62rem 1rem;
  margin-bottom: .35rem;
}
.presentation-hero .hero-kicker { display:none; }
.presentation-hero h1 { font-size: 1.52rem; margin:0; }
.presentation-hero p { font-size:.72rem; margin:.12rem 0 0; }
.presentation-hero-row { display:flex; align-items:center; justify-content:space-between; gap:1rem; }
.presentation-hero-tag { color:#667085; font-size:.7rem; white-space:nowrap; }
.source-mini-badge { display:inline-flex; align-items:center; gap:.35rem; border-radius:999px; padding:.18rem .48rem; font-size:.62rem; font-weight:760; background:#ECFDF3; color:#027A48; }
.source-mini-badge.warn { background:#FFFAEB; color:#B54708; }
.source-mini-badge.error { background:#FFF1F3; color:#C01048; }
.source-mini-dot { width:6px; height:6px; border-radius:50%; background:currentColor; display:inline-block; }
.demo-insight { border:1px solid #B2DDFF; background:#EFF8FF; border-radius:14px; padding:.72rem .85rem; margin:.5rem 0 .7rem; }
.demo-insight strong { color:#175CD3; }
.demo-insight div { color:#344054; font-size:.78rem; line-height:1.4; }
.monitor-flow { display:grid; grid-template-columns:1.1fr 1fr 1fr 1.1fr; gap:.45rem; margin:.55rem 0 .8rem; }
.monitor-step { position:relative; border:1px solid #E4E7EC; background:#FFFFFF; border-radius:14px; padding:.72rem .78rem; min-height:88px; }
.monitor-step:not(:last-child):after { content:'→'; position:absolute; right:-.38rem; top:50%; transform:translateY(-50%); color:#98A2B3; font-weight:800; z-index:2; }
.monitor-step-code { color:#98A2B3; font-size:.58rem; font-weight:760; letter-spacing:.06em; text-transform:uppercase; }
.monitor-step-title { color:#344054; font-size:.76rem; font-weight:720; margin-top:.18rem; }
.monitor-step-value { color:#101828; font-size:1rem; font-weight:780; margin-top:.28rem; }
.monitor-step-note { color:#667085; font-size:.65rem; margin-top:.18rem; line-height:1.3; }
.presentation-note { color:#98A2B3; font-size:.66rem; margin-top:.35rem; }
@media (max-width: 1000px) {
  .monitor-flow { grid-template-columns:1fr 1fr; }
  .monitor-step:not(:last-child):after { display:none; }
}

/* v0.9.5 final presentation polish */
.risk-ladder {
  display:grid; grid-template-columns:1.15fr .72fr 1.15fr .72fr 1.15fr; gap:.42rem;
  align-items:stretch; margin:.35rem 0 .45rem;
}
.risk-ladder-step, .risk-ladder-threshold {
  border:1px solid #E4E7EC; border-radius:14px; background:#FFFFFF;
  padding:.62rem .7rem; min-width:0;
}
.risk-ladder-step.mr1 { border-top:3px solid #32D583; }
.risk-ladder-step.mr2 { border-top:3px solid #F79009; }
.risk-ladder-step.mr3 { border-top:3px solid #F04438; }
.risk-ladder-code { color:#667085; font-size:.62rem; font-weight:780; letter-spacing:.05em; }
.risk-ladder-value { color:#101828; font-size:1.12rem; font-weight:800; margin-top:.08rem; }
.risk-ladder-note { color:#667085; font-size:.65rem; margin-top:.12rem; line-height:1.25; }
.risk-ladder-threshold { background:#F9FAFB; display:flex; flex-direction:column; justify-content:center; text-align:center; }
.risk-ladder-threshold .risk-ladder-code { color:#B54708; }
.risk-ladder-threshold .risk-ladder-value { font-size:.94rem; }
.risk-ladder-threshold .risk-ladder-note { font-size:.61rem; }
.presentation-section-note { color:#667085; font-size:.67rem; margin:.15rem 0 .32rem; }
@media (max-width: 1100px) {
  .risk-ladder { grid-template-columns:1fr; }
  .risk-ladder-threshold { text-align:left; }
}


/* Lazy top navigation: only the selected page is rendered. */
[data-testid="stRadio"] > div[role="radiogroup"] {
  display:flex; gap:1.25rem; align-items:center;
  border-bottom:1px solid var(--line); margin:.05rem 0 .55rem;
}
[data-testid="stRadio"] > div[role="radiogroup"] label {
  margin:0; padding:.45rem 0 .5rem; font-weight:650; cursor:pointer;
}
[data-testid="stRadio"] > div[role="radiogroup"] label > div:first-child { display:none; }
</style>
""",
    unsafe_allow_html=True,
)


def status_chip(status: SourceStatus) -> str:
    labels = {
        "live": "актуальный URL",
        "upload": "ручной файл",
        "cache": "последний успешный снимок",
        "fallback": "встроенный снимок",
        "manual": "сохранённый ручной файл",
        "missing": "нет источника",
        "error": "ошибка",
    }
    label = labels.get(status.state, status.state)
    return (
        f'<span class="status-chip status-{html.escape(status.state)}" '
        f'title="{html.escape(status.detail)}">{html.escape(status.name)}: {html.escape(label)}</span>'
    )


def metric_card(
    label: str,
    value: str,
    note: str = "",
    *,
    code: str = "",
    tone: str = "",
    compact: bool = False,
) -> None:
    classes = "metric-card"
    if tone:
        classes += f" {html.escape(tone)}"
    if compact:
        classes += " compact"
    code_html = f'<div class="metric-code">{html.escape(code)}</div>' if code else ""
    note_html = f'<div class="metric-note">{html.escape(note)}</div>' if note else ""
    st.markdown(
        f"""
<div class="{classes}">
  {code_html}
  <div class="metric-label">{html.escape(label)}</div>
  <div class="metric-value">{html.escape(value)}</div>
  {note_html}
</div>
""",
        unsafe_allow_html=True,
    )


def _as_float(value: object) -> float | None:
    if is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def contracts_at_limit(limit_ba: object, lot_volume: object) -> float | None:
    limit_number = _as_float(limit_ba)
    lot_number = _as_float(lot_volume)
    if limit_number is None or lot_number is None or lot_number <= 0:
        return None
    return limit_number / lot_number


def kpi_card(label: str, value: str, meta: str, tone: str = "market") -> None:
    st.markdown(
        f"""
<div class="kpi-card {html.escape(tone)}">
  <div class="kpi-label">{html.escape(label)}</div>
  <div class="kpi-value">{html.escape(value)}</div>
  <div class="kpi-meta">{html.escape(meta)}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def parameter_strip(items: list[tuple[str, str, str, str]]) -> None:
    blocks = []
    for code, label, value, note in items:
        blocks.append(
            f'<div class="parameter-item"><div class="parameter-code">{html.escape(code)}</div>'
            f'<div class="parameter-label">{html.escape(label)}</div>'
            f'<div class="parameter-value">{html.escape(value)}</div>'
            f'<div class="parameter-note">{html.escape(note)}</div></div>'
        )
    columns = max(1, len(items))
    st.markdown(f'<div class="parameter-strip" style="grid-template-columns:repeat({columns},minmax(0,1fr))">' + ''.join(blocks) + '</div>', unsafe_allow_html=True)


def concentration_scale(mr1: object, mr2: object, mr3: object, lk1_contracts: object, lk2_contracts: object) -> None:
    st.markdown(
        f"""
<div class="concentration-wrap">
  <div class="small-muted">Повышенная ставка применяется только к превышению соответствующего порога.</div>
  <div class="concentration-axis"><div class="mr1-seg"></div><div class="mr2-seg"></div><div class="mr3-seg"></div></div>
  <div class="concentration-labels">
    <div class="concentration-label"><strong>MR1 · {html.escape(fmt_rate(mr1))}</strong>до LK1 ≈ {html.escape(fmt_number(lk1_contracts, 0))} контр.</div>
    <div class="concentration-label"><strong>MR2 · {html.escape(fmt_rate(mr2))}</strong>сверх LK1 до LK2 ≈ {html.escape(fmt_number(lk2_contracts, 0))} контр.</div>
    <div class="concentration-label"><strong>MR3 · {html.escape(fmt_rate(mr3))}</strong>часть позиции сверх LK2</div>
  </div>
  <div class="concentration-thresholds">
    <span>0</span><span>LK1 ≈ {html.escape(fmt_number(lk1_contracts, 0))}</span><span>LK2 ≈ {html.escape(fmt_number(lk2_contracts, 0))}</span><span>позиция →</span>
  </div>
  <div class="scale-note">Шкала схематическая и показывает логику ступеней, а не пропорциональный масштаб диапазонов.</div>
</div>
""",
        unsafe_allow_html=True,
    )


def concentration_ladder(
    mr1: object, mr2: object, mr3: object,
    lk1_contracts: object, lk2_contracts: object,
    lk1_rub: object, lk2_rub: object,
) -> None:
    """Compact presentation view of the stepped concentration model."""
    st.markdown(
        f"""
<div class="risk-ladder">
  <div class="risk-ladder-step mr1">
    <div class="risk-ladder-code">MR1 · БАЗОВЫЙ УРОВЕНЬ</div>
    <div class="risk-ladder-value">{html.escape(fmt_rate(mr1))}</div>
    <div class="risk-ladder-note">До LK1 · ≈ {html.escape(fmt_number(lk1_contracts, 0))} контрактов</div>
  </div>
  <div class="risk-ladder-threshold">
    <div class="risk-ladder-code">LK1</div>
    <div class="risk-ladder-value">{html.escape(fmt_number(lk1_contracts, 0))} контр.</div>
    <div class="risk-ladder-note">≈ {html.escape(fmt_compact_rub(lk1_rub))}</div>
  </div>
  <div class="risk-ladder-step mr2">
    <div class="risk-ladder-code">MR2 · ПОВЫШЕННЫЙ УРОВЕНЬ</div>
    <div class="risk-ladder-value">{html.escape(fmt_rate(mr2))}</div>
    <div class="risk-ladder-note">Только часть сверх LK1 и до LK2</div>
  </div>
  <div class="risk-ladder-threshold">
    <div class="risk-ladder-code">LK2</div>
    <div class="risk-ladder-value">{html.escape(fmt_number(lk2_contracts, 0))} контр.</div>
    <div class="risk-ladder-note">≈ {html.escape(fmt_compact_rub(lk2_rub))}</div>
  </div>
  <div class="risk-ladder-step mr3">
    <div class="risk-ladder-code">MR3 · ВЫСОКАЯ КОНЦЕНТРАЦИЯ</div>
    <div class="risk-ladder-value">{html.escape(fmt_rate(mr3))}</div>
    <div class="risk-ladder-note">Только часть позиции сверх LK2</div>
  </div>
</div>
<div class="presentation-section-note">Повышенная ставка не пересчитывает всю позицию: следующий уровень применяется только к превышению соответствующего LK.</div>
""",
        unsafe_allow_html=True,
    )


def price_corridor(low: object, price: object, high: object, width: object, decimals: int | None) -> None:
    low_n, price_n, high_n = _as_float(low), _as_float(price), _as_float(high)
    marker = 50.0
    to_low = to_high = low_pct = high_pct = None
    if low_n is not None and price_n is not None and high_n is not None and high_n > low_n:
        marker = max(0.0, min(100.0, (price_n - low_n) / (high_n - low_n) * 100.0))
        to_low = price_n - low_n
        to_high = high_n - price_n
        if price_n != 0:
            low_pct = to_low / abs(price_n)
            high_pct = to_high / abs(price_n)
    st.markdown(
        f"""
<div class="corridor-card">
  <div class="corridor-top">
    <div><div class="corridor-edge-label">LOWLIMIT</div><div class="corridor-edge-value">{html.escape(fmt_number(low, decimals))}</div></div>
    <div style="text-align:center"><div class="corridor-edge-label">ТЕКУЩАЯ ЦЕНА</div><div class="corridor-edge-value">{html.escape(fmt_number(price, decimals))}</div></div>
    <div style="text-align:right"><div class="corridor-edge-label">HIGHLIMIT</div><div class="corridor-edge-value">{html.escape(fmt_number(high, decimals))}</div></div>
  </div>
  <div class="corridor-track"><div class="corridor-marker" style="left:{marker:.2f}%"></div></div>
  <div class="corridor-caption"><span>Нижняя граница</span><span>Ширина: {html.escape(fmt_number(width, decimals))}</span><span>Верхняя граница</span></div>
  <div class="corridor-insights">
    <div class="corridor-insight"><div class="corridor-insight-label">До LOWLIMIT</div><div class="corridor-insight-value">{html.escape(fmt_number(to_low, decimals))} · {html.escape(fmt_rate(low_pct))}</div></div>
    <div class="corridor-insight"><div class="corridor-insight-label">Позиция цены в коридоре</div><div class="corridor-insight-value">{marker:.1f}%</div></div>
    <div class="corridor-insight"><div class="corridor-insight-label">До HIGHLIMIT</div><div class="corridor-insight-value">{html.escape(fmt_number(to_high, decimals))} · {html.escape(fmt_rate(high_pct))}</div></div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def monitoring_flow(range_fut: object, day_time: object, total_shifts: object, evening_shifts: object, evening_time: object) -> None:
    day_label = fmt_seconds(day_time)
    evening_label = fmt_seconds(evening_time)
    st.markdown(
        f"""
<div class="monitor-flow">
  <div class="monitor-step"><div class="monitor-step-code">1 · CONTROL AREA</div><div class="monitor-step-title">Цена входит в контрольную область</div><div class="monitor-step-value">RangeFut {html.escape(fmt_rate(range_fut))}</div><div class="monitor-step-note">Параметр ширины контрольного коридора НКЦ.</div></div>
  <div class="monitor-step"><div class="monitor-step-code">2 · TIMER</div><div class="monitor-step-title">Условие сохраняется</div><div class="monitor-step-value">{html.escape(day_label)}</div><div class="monitor-step-note">Днём · вечером {html.escape(evening_label)}.</div></div>
  <div class="monitor-step"><div class="monitor-step-code">3 · LIMIT</div><div class="monitor-step-title">Проверяется доступный лимит</div><div class="monitor-step-value">{html.escape(fmt_integer(total_shifts))} всего</div><div class="monitor-step-note">Вечерний лимит: {html.escape(fmt_integer(evening_shifts))}.</div></div>
  <div class="monitor-step"><div class="monitor-step-code">4 · AUTO SHIFT</div><div class="monitor-step-title">Граница может быть раздвинута</div><div class="monitor-step-value">Автоматически</div><div class="monitor-step-note">Если условия контроля выполнены и лимит сдвигов не исчерпан.</div></div>
</div>
<div class="presentation-note">Схема показывает логику механизма; геометрические размеры блоков не являются масштабом RangeFut.</div>
""",
        unsafe_allow_html=True,
    )


def position_bar(level1: object, level2: object, level3: object) -> None:
    values = [max(0.0, _as_float(v) or 0.0) for v in (level1, level2, level3)]
    total = sum(values)
    shares = [v / total * 100.0 if total > 0 else 0.0 for v in values]
    min_widths = ["min-width:8px;" if v > 0 else "" for v in values]
    st.markdown(
        f"""
<div class="calc-hero">
  <div class="small-muted">Разбиение введённой позиции по уровням концентрации</div>
  <div class="position-bar">
    <div class="position-mr1" style="width:{shares[0]:.4f}%;{min_widths[0]}"></div>
    <div class="position-mr2" style="width:{shares[1]:.4f}%;{min_widths[1]}"></div>
    <div class="position-mr3" style="width:{shares[2]:.4f}%;{min_widths[2]}"></div>
  </div>
  <div class="position-legend">
    <span><i class="legend-dot legend-mr1"></i>MR1 · {html.escape(fmt_number(values[0], 0))} контр.</span>
    <span><i class="legend-dot legend-mr2"></i>MR2 · {html.escape(fmt_number(values[1], 0))} контр.</span>
    <span><i class="legend-dot legend-mr3"></i>MR3 · {html.escape(fmt_number(values[2], 0))} контр.</span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def set_session_number(key: str, value: int) -> None:
    st.session_state[key] = max(0, int(value))


def open_contract_from_monitor(assetcode: str, secid: str) -> None:
    code = str(assetcode or "").strip()
    if not code:
        return
    st.session_state["selected_assetcode"] = code
    if secid:
        st.session_state[f"selected_contract_{code}"] = str(secid)
    st.session_state["active_page"] = "Обзор"


def cell_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def yes_no_text(value: object) -> str:
    if value is None or pd.isna(value):
        return "—"
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"да", "true", "1", "yes"}:
            return "Да"
        if text in {"нет", "false", "0", "no"}:
            return "Нет"
        return "—"
    try:
        return "Да" if bool(value) else "Нет"
    except Exception:
        return "—"


def fmt_seconds(value: object) -> str:
    if is_missing(value):
        return "—"
    seconds = int(round(float(value)))
    if seconds % 60 == 0:
        return f"{seconds // 60} мин"
    return f"{seconds} сек"


def special_override(
    rows: pd.DataFrame,
    parameter: str,
    standard_value: object,
) -> tuple[object, pd.Series | None]:
    """Return the active calendar value, including an intentional dash."""
    if rows.empty or "parameter" not in rows.columns:
        return standard_value, None
    matches = rows[rows["parameter"] == parameter]
    if matches.empty:
        return standard_value, None
    row = matches.iloc[-1]
    return row.get("value"), row


def special_parameter_note(
    override_row: pd.Series | None,
    standard_note: str,
) -> str:
    if override_row is None:
        return standard_note
    event = cell_text(override_row.get("event_name"))
    end_at = override_row.get("end_at")
    period = ""
    if end_at is not None and not pd.isna(end_at):
        period = f" · до {pd.Timestamp(end_at).strftime('%d.%m.%Y %H:%M')} МСК"
    event_part = f" · {event}" if event else ""
    return f"Специальное значение по календарю НКЦ{event_part}{period}"


def parameter_source(override_row: pd.Series | None, standard_source: str) -> str:
    return "Календарь специальных риск-параметров НКЦ" if override_row is not None else standard_source


def evening_limit_note(row: pd.Series | None, status: SourceStatus) -> str:
    """Explain the source and meaning of the official evening shift limit."""
    if row is None:
        return "AutoShiftNumMREvg не получен"
    source_hint = {
        "live": "официальный XLSX НКЦ",
        "cache": "последний успешный снимок официального XLSX",
        "fallback": "встроенная официальная копия XLSX НКЦ",
        "manual": "сохранённый ручной файл",
        "upload": "ручной файл",
    }.get(status.state, "источник вечерних параметров")
    return f"{source_hint}; фактический предел также ограничен остатком общего AutoShiftNumMR"


@st.cache_data(ttl=300, show_spinner=False)
def load_from_config() -> tuple[pd.DataFrame, SourceStatus, pd.DataFrame, SourceStatus, pd.DataFrame, SourceStatus]:
    market, market_status = load_dataset(
        name="MR/LK",
        env_url_name="NCC_MARKET_RATES_CSV_URL",
        fallback_path=None,
        cache_path=BASE_DIR / "runtime_cache" / "market_rates_last_good.csv",
        manual_path=BASE_DIR / "runtime_cache" / "market_rates_manual.csv",
        required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
        default_url=MARKET_RATES_JSON_URL,
        alternate_urls=[MARKET_RATES_CSV_URL],
    )
    static, static_status = load_dataset(
        name="Статические параметры",
        env_url_name="NCC_STATIC_PARAMS_CSV_URL",
        fallback_path=DATA_DIR / "static_params_fallback.csv",
        cache_path=BASE_DIR / "runtime_cache" / "static_params_last_good.csv",
        manual_path=BASE_DIR / "runtime_cache" / "static_params_manual.csv",
        required_columns={"assetcode", "autoshiftnummr", "futmontime", "rangefut"},
        default_url=STATIC_PARAMS_JSON_URL,
        alternate_urls=[STATIC_PARAMS_CSV_URL],
    )
    extra, extra_status = load_evening_dataset(
        fallback_path=DATA_DIR / "evening_static_params_2026-08-03.xlsx",
        cache_path=BASE_DIR / "runtime_cache" / "evening_params_last_good.csv",
        manual_path=BASE_DIR / "runtime_cache" / "evening_params_manual.csv",
    )
    return market, market_status, static, static_status, extra, extra_status


@st.cache_data(ttl=300, show_spinner=False)
def load_offdays_cached() -> tuple[pd.DataFrame, SourceStatus]:
    return load_offdays_dataset(
        fallback_path=DATA_DIR / "offdays_params_fallback.csv",
        cache_path=BASE_DIR / "runtime_cache" / "offdays_last_good.xlsx",
        manual_path=BASE_DIR / "runtime_cache" / "offdays_manual.csv",
    )


@st.cache_data(ttl=60, show_spinner=False)
def load_contracts_cached() -> tuple[pd.DataFrame, SourceStatus]:
    return load_forts_contracts(
        cache_path=BASE_DIR / "runtime_cache" / "forts_contracts_last_good.json"
    )


@st.cache_data(ttl=1800, show_spinner=False)
def load_special_calendar_cached() -> tuple[pd.DataFrame, SourceStatus]:
    return load_special_calendar_dataset(
        fallback_path=DATA_DIR / "special_risk_calendar_2026.xlsx",
        cache_path=BASE_DIR / "runtime_cache" / "special_risk_calendar_last_good.xlsx",
        manual_path=BASE_DIR / "runtime_cache" / "special_risk_calendar_manual.xlsx",
    )


@st.cache_data(ttl=1800, show_spinner=False)
def load_collateral_cached():
    # Optional enrichment source. It is lazy and is not part of the six
    # critical futures datasets, so its failure never hides the core dashboard.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(load_collateral_sources, base_dir=BASE_DIR, data_dir=DATA_DIR)
        return future.result()


SOURCE_BUNDLE_KEY = "_source_bundle_v096"
COLLATERAL_BUNDLE_KEY = "_collateral_bundle_v11"

@st.cache_data(ttl=300, show_spinner=False)
def load_all_sources_cached():
    """Load independent official sources in parallel.

    Ordinary widget interactions reuse a session snapshot and therefore never
    trigger network I/O. This cached parallel loader is used on the first run
    of a browser session and after an explicit refresh.
    """
    jobs = {
        "market": lambda: load_dataset(
            name="MR/LK",
            env_url_name="NCC_MARKET_RATES_CSV_URL",
            fallback_path=None,
            cache_path=BASE_DIR / "runtime_cache" / "market_rates_last_good.csv",
            manual_path=BASE_DIR / "runtime_cache" / "market_rates_manual.csv",
            required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
            default_url=MARKET_RATES_JSON_URL,
            alternate_urls=[MARKET_RATES_CSV_URL],
        ),
        "static": lambda: load_dataset(
            name="Статические параметры",
            env_url_name="NCC_STATIC_PARAMS_CSV_URL",
            fallback_path=DATA_DIR / "static_params_fallback.csv",
            cache_path=BASE_DIR / "runtime_cache" / "static_params_last_good.csv",
            manual_path=BASE_DIR / "runtime_cache" / "static_params_manual.csv",
            required_columns={"assetcode", "autoshiftnummr", "futmontime", "rangefut"},
            default_url=STATIC_PARAMS_JSON_URL,
            alternate_urls=[STATIC_PARAMS_CSV_URL],
        ),
        "evening": lambda: load_evening_dataset(
            fallback_path=DATA_DIR / "evening_static_params_2026-08-03.xlsx",
            cache_path=BASE_DIR / "runtime_cache" / "evening_params_last_good.csv",
            manual_path=BASE_DIR / "runtime_cache" / "evening_params_manual.csv",
        ),
        "offdays": lambda: load_offdays_dataset(
            fallback_path=DATA_DIR / "offdays_params_fallback.csv",
            cache_path=BASE_DIR / "runtime_cache" / "offdays_last_good.xlsx",
            manual_path=BASE_DIR / "runtime_cache" / "offdays_manual.csv",
        ),
        "contracts": lambda: load_forts_contracts(
            cache_path=BASE_DIR / "runtime_cache" / "forts_contracts_last_good.json"
        ),
        "special": lambda: load_special_calendar_dataset(
            fallback_path=DATA_DIR / "special_risk_calendar_2026.xlsx",
            cache_path=BASE_DIR / "runtime_cache" / "special_risk_calendar_last_good.xlsx",
            manual_path=BASE_DIR / "runtime_cache" / "special_risk_calendar_manual.xlsx",
        ),
    }
    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="moex-source") as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        results = {name: future.result() for name, future in futures.items()}
    market, market_status = results["market"]
    static, static_status = results["static"]
    evening, evening_status = results["evening"]
    offdays, offdays_status = results["offdays"]
    contracts, contracts_status = results["contracts"]
    special, special_status = results["special"]
    return (
        market, market_status, static, static_status, evening, evening_status,
        offdays, offdays_status, contracts, contracts_status, special, special_status,
    )

if PUBLIC_DEPLOYMENT:
    presentation_mode = True
    st.sidebar.markdown("### MOEX Risk Dashboard")
    st.sidebar.caption("Публичная версия · официальные источники MOEX / НКЦ")
else:
    presentation_mode = st.sidebar.toggle(
        "Режим презентации",
        value=True,
        key="presentation_mode",
        help="Скрывает диагностику и ручные загрузчики, оставляя только интерфейс для демонстрации.",
    )
    st.sidebar.caption("Отключите режим, если нужно проверить источники или загрузить файлы вручную.")

if presentation_mode:
    st.markdown(
        """
<div class="hero presentation-hero">
  <div class="presentation-hero-row">
    <div><h1>MOEX Risk Dashboard</h1><p>Практическая аналитика риск-параметров фьючерсов MOEX / НКЦ.</p></div>
    <div class="presentation-hero-tag">DERIVATIVES RISK ANALYTICS</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    # In presentation mode the technical upload controls stay out of the main view.
    market_upload = static_upload = extra_upload = special_calendar_upload = None
    st.markdown(
        """
<style>
.block-container { padding-top:.30rem; }
.presentation-hero { padding:.48rem .9rem; margin-bottom:.22rem; }
.presentation-hero h1 { font-size:1.42rem; }
.presentation-hero p { font-size:.68rem; }
.section-head { margin:.55rem 0 .26rem; }
.section-title { font-size:1rem; }
.section-subtitle { font-size:.68rem; }
.kpi-card { min-height:82px; padding:.52rem .68rem .48rem; }
.kpi-value { font-size:1.28rem; margin-top:.18rem; }
.kpi-meta { font-size:.61rem; margin-top:.18rem; }
.parameter-strip { margin:.28rem 0 .42rem; }
.parameter-item { padding:.50rem .64rem; min-height:78px; }
.parameter-value { font-size:.96rem; }
.selection-compact { margin:.22rem 0 .35rem; padding:.48rem .68rem; }
.selection-title { font-size:.84rem; }
.selection-time { font-size:.62rem; }
[data-testid="stSelectbox"] { margin-bottom:-.48rem; }
[data-testid="stTabs"] { margin-bottom:-.1rem; }
</style>
""",
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
<div class="hero">
  <div class="hero-kicker">MOEX · NCC · DERIVATIVES RISK ANALYTICS</div>
  <h1>MOEX Risk Dashboard</h1>
  <p>Практическая аналитика риск-параметров фьючерсов MOEX / НКЦ.</p>
</div>
""",
        unsafe_allow_html=True,
    )

    with st.expander("Источники данных и ручная загрузка", expanded=False):
        st.caption(
            "MR/LK и статические параметры загружаются автоматически из MOEX ISS. "
            "OffDaysTradingPriceRangeShift загружается из официального XLSX НКЦ со страницы риск-параметров. "
            "Календарь специальных риск-параметров загружается из официального XLSX НКЦ; актуальный файл 2026 встроен как резервная копия. "
            "Вечерние AutoShiftNumMREvg и FutMonTimeEvg загружаются из официального XLSX НКЦ «Статические параметры, применяемые в вечернюю сессию». "
            "Текущие цены, LAST_RUB, STEPPRICE, MINSTEP, HIGHLIMIT и LOWLIMIT "
            "загружаются из официального метода MOEX ISS по контрактам FORTS. MR/LK и статические параметры можно загрузить вручную как CSV/XLSX; валидная ручная копия сохраняется локально."
        )
        st.caption("При облачном размещении локальные runtime-cache и ручные файлы являются временными и могут быть сброшены при перезапуске инстанса.")
        u1, u2, u3, u4 = st.columns(4)
        with u1:
            market_upload = st.file_uploader("MR1–MR3 и LK1–LK2", type=["csv", "xlsx"], key="market_upload")
        with u2:
            static_upload = st.file_uploader("Статические параметры", type=["csv", "xlsx"], key="static_upload")
        with u3:
            extra_upload = st.file_uploader("Вечерние параметры / ручная подмена", type=["csv", "xlsx"], key="extra_upload")
        with u4:
            special_calendar_upload = st.file_uploader(
                "Календарь специальных параметров",
                type=["xlsx"],
                key="special_calendar_upload",
                help="Официальный XLSX НКЦ. Загруженный файл имеет приоритет над ссылкой на сайте НКЦ.",
            )
        st.download_button(
            "Шаблон MR/LK",
            data=(DATA_DIR / "market_rates_template.csv").read_bytes(),
            file_name="market_rates_template.csv",
            mime="text/csv",
        )
        st.download_button(
            "Шаблон дополнительных параметров",
            data=(DATA_DIR / "extra_params_template.csv").read_bytes(),
            file_name="extra_params_template.csv",
            mime="text/csv",
        )

if SOURCE_BUNDLE_KEY not in st.session_state:
    st.session_state[SOURCE_BUNDLE_KEY] = load_all_sources_cached()
(
    market_df, market_status, static_df, static_status, extra_df, extra_status,
    offdays_df, offdays_status, contracts_df, contracts_status,
    special_calendar_df, special_calendar_status,
) = st.session_state[SOURCE_BUNDLE_KEY]

# Uploaded data has priority over configured/live/fallback sources.
if market_upload is not None:
    market_df, market_status = load_dataset(
        name="MR/LK",
        env_url_name="NCC_MARKET_RATES_CSV_URL",
        cache_path=BASE_DIR / "runtime_cache" / "market_rates_last_good.csv",
        manual_path=BASE_DIR / "runtime_cache" / "market_rates_manual.csv",
        required_columns={"assetcode", "mr1", "mr2", "mr3", "lk1", "lk2"},
        uploaded_file=market_upload,
        default_url=MARKET_RATES_JSON_URL,
        alternate_urls=[MARKET_RATES_CSV_URL],
    )
if static_upload is not None:
    static_df, static_status = load_dataset(
        name="Статические параметры",
        env_url_name="NCC_STATIC_PARAMS_CSV_URL",
        fallback_path=DATA_DIR / "static_params_fallback.csv",
        cache_path=BASE_DIR / "runtime_cache" / "static_params_last_good.csv",
        manual_path=BASE_DIR / "runtime_cache" / "static_params_manual.csv",
        required_columns={"assetcode", "autoshiftnummr", "futmontime", "rangefut"},
        uploaded_file=static_upload,
        default_url=STATIC_PARAMS_JSON_URL,
        alternate_urls=[STATIC_PARAMS_CSV_URL],
    )
if extra_upload is not None:
    extra_df, extra_status = load_evening_dataset(
        fallback_path=DATA_DIR / "evening_static_params_2026-08-03.xlsx",
        cache_path=BASE_DIR / "runtime_cache" / "evening_params_last_good.csv",
        manual_path=BASE_DIR / "runtime_cache" / "evening_params_manual.csv",
        uploaded_file=extra_upload,
    )
if special_calendar_upload is not None:
    special_calendar_df, special_calendar_status = load_special_calendar_dataset(
        uploaded_file=special_calendar_upload,
        fallback_path=DATA_DIR / "special_risk_calendar_2026.xlsx",
        cache_path=BASE_DIR / "runtime_cache" / "special_risk_calendar_last_good.xlsx",
        manual_path=BASE_DIR / "runtime_cache" / "special_risk_calendar_manual.xlsx",
    )

def refresh_data() -> None:
    # Всегда обновляем снимок текущей browser-сессии. В публичном deployment
    # не очищаем глобальный st.cache_data: это защищает MOEX/NCC от лишних
    # запросов и не позволяет одному посетителю сбрасывать кэш для остальных.
    st.session_state.pop(SOURCE_BUNDLE_KEY, None)
    st.session_state.pop(COLLATERAL_BUNDLE_KEY, None)
    if not PUBLIC_DEPLOYMENT:
        load_all_sources_cached.clear()
        load_from_config.clear()
        load_offdays_cached.clear()
        load_contracts_cached.clear()
        load_special_calendar_cached.clear()
        load_collateral_cached.clear()


if presentation_mode:
    st.sidebar.button("🔄 Обновить данные", on_click=refresh_data, use_container_width=True)
    if PUBLIC_DEPLOYMENT:
        st.sidebar.caption("Обновляет снимок вашей сессии. Общий сетевой кэш ограничивает частоту запросов к MOEX / НКЦ.")
    else:
        st.sidebar.caption("Обычные клики используют снимок текущей сессии и не запускают сетевые запросы.")

source_statuses = [
    market_status, static_status, contracts_status, offdays_status,
    extra_status, special_calendar_status,
]
source_error = any(status.state in {"error", "missing"} for status in source_statuses)
source_all_live = all(status.state == "live" for status in source_statuses)
source_live_count = sum(status.state == "live" for status in source_statuses)
source_available_count = sum(status.state not in {"error", "missing"} for status in source_statuses)
source_snapshot_count = sum(status.state in {"cache", "fallback", "manual", "upload"} for status in source_statuses)
source_dot = "error" if source_error else ""
source_label = "DATA ERROR" if source_error else ("DATA LIVE" if source_all_live else "DATA READY")
source_meta_parts = [
    f"{source_available_count}/{len(source_statuses)} наборов доступны",
    f"{source_live_count} live",
]
if source_snapshot_count:
    source_meta_parts.append(f"{source_snapshot_count} из снимка/кеша")
source_meta = " · ".join(source_meta_parts)
if not presentation_mode:
    st.markdown(
        f'<div class="summary-strip"><div class="summary-left"><span class="summary-dot {source_dot}"></span>'
        f'<span class="summary-title">{html.escape(source_label)}</span><span class="summary-meta">{html.escape(source_meta)}</span></div>'
        '<span class="summary-meta">подробности ниже</span></div>',
        unsafe_allow_html=True,
    )

    with st.expander("Состояние источников, обновление и диагностика", expanded=False):
        st.button("Обновить данные", icon="🔄", on_click=refresh_data)
        st.markdown(
            '<div class="status-row">'
            + status_chip(market_status)
            + status_chip(static_status)
            + status_chip(offdays_status)
            + status_chip(extra_status)
            + status_chip(contracts_status)
            + status_chip(special_calendar_status)
            + "</div>",
            unsafe_allow_html=True,
        )
        source_state_labels = {
            "live": "Актуальный API/URL",
            "upload": "Ручная загрузка",
            "cache": "Последний успешный снимок",
            "fallback": "Встроенный fallback",
            "manual": "Сохранённый ручной файл",
            "missing": "Нет источника",
            "error": "Ошибка",
        }
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Набор данных": status.name,
                        "Статус": source_state_labels.get(status.state, status.state),
                        "Последнее обновление / снимок": status.updated_at or "—",
                        "Источник / причина": status.detail,
                    }
                    for status in source_statuses
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Новый ответ заменяет last-good кеш только после успешного разбора и проверки обязательных колонок. "
            "Пустой ответ, HTTP-ошибка или невалидный ручной файл не затирают подтверждённый снимок."
        )


NAV_PAGES = ["Мониторинг", "Обзор", "Границы", "Калькулятор ГО", "Спецрежимы НКЦ", "Методика"]
if st.session_state.get("active_page") not in NAV_PAGES:
    st.session_state["active_page"] = "Мониторинг"
active_page = st.radio(
    "Раздел", NAV_PAGES, horizontal=True, key="active_page", label_visibility="collapsed"
)

security_collateral_df = pd.DataFrame()
asset_collateral_df = pd.DataFrame()
security_collateral_status = SourceStatus("Short/Collateral · ценные бумаги", "missing", "источник не запрашивался")
asset_collateral_status = SourceStatus("Short/Collateral · валюта/металлы", "missing", "источник не запрашивался")
if active_page == "Обзор":
    if COLLATERAL_BUNDLE_KEY not in st.session_state:
        st.session_state[COLLATERAL_BUNDLE_KEY] = load_collateral_cached()
    (
        security_collateral_df, security_collateral_status,
        asset_collateral_df, asset_collateral_status,
    ) = st.session_state[COLLATERAL_BUNDLE_KEY]

assetcodes = union_assetcodes(market_df, static_df, offdays_df, extra_df, contracts_df)
if not assetcodes:
    st.error("Не найден ни один assetcode. Проверьте источники данных или загрузите ручной файл.")
    st.stop()

# Явно храним выбор пользователя. Без key после обновления источников
# Streamlit мог пересоздать selectbox и применить индекс по умолчанию RTS,
# тогда как в браузере ещё отображалось прежнее значение.
fallback_assetcode = "RTS" if "RTS" in assetcodes else assetcodes[0]
if "selected_assetcode" not in st.session_state:
    st.session_state["selected_assetcode"] = fallback_assetcode
elif st.session_state["selected_assetcode"] not in assetcodes:
    st.session_state["selected_assetcode"] = fallback_assetcode

# В списке показываем не только технический код, но и название базисного актива
# из официальной выгрузки MR/LK. Это особенно важно для похожих кодов T и TCSI.
asset_labels: dict[str, str] = {}
for code in assetcodes:
    row = latest_row(market_df, code)
    title = None if row is None else row.get("title")
    if title is not None and not pd.isna(title) and str(title).strip():
        asset_labels[code] = f"{code} — {str(title).strip()}"
    else:
        asset_labels[code] = code

if active_page == "Мониторинг":
    st.markdown(
        '<div class="section-head"><div class="section-kicker">Risk Radar</div>'
        '<div class="section-title">Мониторинг фьючерсов по близости к ценовым границам</div>'
        '<div class="section-subtitle">Один ближайший активный контракт на каждый БА. Статусы WATCH/CRITICAL — аналитические пороги интерфейса, а не официальные термины НКЦ.</div></div>',
        unsafe_allow_html=True,
    )

    f1, f2, f3 = st.columns([1.15, 1, 1], gap="large")
    with f1:
        attention_threshold = st.slider(
            "Порог внимания до ближайшей границы, %",
            min_value=0.5, max_value=10.0, value=2.0, step=0.25,
            key="monitor_attention_threshold",
            help="Аналитический фильтр дашборда. Он не заменяет официальный RangeFut НКЦ.",
        )
    monitor_df = build_market_monitor(
        contracts_df, market_df, special_calendar_df,
        check_at=datetime.now(ZoneInfo("Europe/Moscow")),
        attention_threshold_pct=attention_threshold,
        critical_threshold_pct=min(0.75, attention_threshold / 2),
    )
    group_options = ["Все группы"] + monitor_groups(monitor_df)
    with f2:
        selected_monitor_group = st.selectbox("Группа инструментов", group_options, key="monitor_group")
    with f3:
        selected_monitor_filter = st.selectbox(
            "Показывать",
            ["Все", "Требуют внимания", "К верхней границе", "К нижней границе", "Special mode"],
            key="monitor_filter",
        )

    if monitor_df.empty:
        st.warning("Не удалось построить мониторинг: в текущем снимке нет контрактов с доступными ценовыми границами.")
        st.stop()

    filtered_monitor = monitor_df.copy()
    if selected_monitor_group != "Все группы":
        filtered_monitor = filtered_monitor[filtered_monitor["group"] == selected_monitor_group]
    attention_states = {"WATCH", "CRITICAL", "OUTSIDE LOW", "OUTSIDE HIGH"}
    if selected_monitor_filter == "Требуют внимания":
        filtered_monitor = filtered_monitor[filtered_monitor["risk_status"].isin(attention_states)]
    elif selected_monitor_filter == "К верхней границе":
        filtered_monitor = filtered_monitor[filtered_monitor["nearest_side"] == "HIGH"]
    elif selected_monitor_filter == "К нижней границе":
        filtered_monitor = filtered_monitor[filtered_monitor["nearest_side"] == "LOW"]
    elif selected_monitor_filter == "Special mode":
        filtered_monitor = filtered_monitor[filtered_monitor["special_mode"]]

    attention_count = int(monitor_df["risk_status"].isin(attention_states).sum())
    high_attention = int(((monitor_df["nearest_side"] == "HIGH") & monitor_df["risk_status"].isin(attention_states)).sum())
    low_attention = int(((monitor_df["nearest_side"] == "LOW") & monitor_df["risk_status"].isin(attention_states)).sum())
    center_attention = int(((monitor_df["nearest_side"] == "CENTER") & monitor_df["risk_status"].isin(attention_states)).sum())
    special_count = int(monitor_df["special_mode"].sum())
    r1, r2, r3, r4 = st.columns(4, gap="small")
    with r1:
        attention_meta = f"из {len(monitor_df)} отслеживаемых БА" + (f" · равноудалены: {center_attention}" if center_attention else "")
        kpi_card("Требуют внимания", str(attention_count), attention_meta, "risk" if attention_count else "market")
    with r2:
        kpi_card("К верхней границе", str(high_attention), f"≤ {fmt_number(attention_threshold, 2)}% до HIGH", "risk" if high_attention else "market")
    with r3:
        kpi_card("К нижней границе", str(low_attention), f"≤ {fmt_number(attention_threshold, 2)}% до LOW", "risk" if low_attention else "market")
    with r4:
        kpi_card("Special mode", str(special_count), "активный календарь НКЦ", "limit" if special_count else "market")

    display = filtered_monitor.reset_index(drop=True).copy()
    status_icons = {
        "OUTSIDE LOW": "🔴 OUTSIDE LOW", "OUTSIDE HIGH": "🔴 OUTSIDE HIGH",
        "CRITICAL": "🔴 CRITICAL", "WATCH": "🟠 WATCH", "NORMAL": "🟢 NORMAL",
    }
    display["Статус"] = display["risk_status"].map(status_icons).fillna(display["risk_status"])
    display["Направление"] = display["nearest_side"].map({"HIGH": "↑ HIGH", "LOW": "↓ LOW", "CENTER": "↔ CENTER"}).fillna("—")
    price_source_labels = {
        "last": "LAST",
        "settleprice": "SETTLE",
        "lastsettleprice": "LAST SETTLE",
        "prevsettleprice": "PREV SETTLE",
    }
    display["Источник цены"] = display["price_source"].map(price_source_labels).fillna("—")
    display["Special"] = display.apply(
        lambda row: ("SPECIAL" + (f" · {row['special_group']}" if row.get("special_group") else "")) if row.get("special_mode") else "",
        axis=1,
    )

    table = display.rename(columns={
        "assetcode": "БА", "group": "Группа", "secid": "Контракт", "price": "Цена",
        "lowlimit": "LOW", "highlimit": "HIGH", "distance_low_pct": "До LOW, %",
        "distance_high_pct": "До HIGH, %", "nearest_pct": "До ближайшей, %",
        "position_pct": "Положение, %",
    })[[
        "Статус", "БА", "Группа", "Контракт", "Цена", "Источник цены", "LOW", "HIGH",
        "До LOW, %", "До HIGH, %", "Направление", "До ближайшей, %", "Положение, %",
        "Special",
    ]]

    event = st.dataframe(
        table, use_container_width=True, hide_index=True, height=min(650, 86 + 35 * max(1, len(table))),
        on_select="rerun", selection_mode="single-row", key="market_monitor_table",
        column_config={
            "Цена": st.column_config.NumberColumn(format="%.4f"),
            "LOW": st.column_config.NumberColumn(format="%.4f"),
            "HIGH": st.column_config.NumberColumn(format="%.4f"),
            "До LOW, %": st.column_config.NumberColumn(format="%.2f%%"),
            "До HIGH, %": st.column_config.NumberColumn(format="%.2f%%"),
            "До ближайшей, %": st.column_config.NumberColumn(format="%.2f%%"),
            "Положение, %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
            "Источник цены": st.column_config.TextColumn(
                help="Поле MOEX ISS, использованное как цена: LAST → SETTLEPRICE → LASTSETTLEPRICE → PREVSETTLEPRICE."
            ),
        },
    )
    selected_rows = []
    try:
        selected_rows = list(event.selection.rows)
    except Exception:
        pass
    if selected_rows:
        selected_idx = selected_rows[0]
        if 0 <= selected_idx < len(display):
            selected = display.iloc[selected_idx]
            st.button(
                f"Открыть {selected['assetcode']} · {selected['secid']} в карточке контракта →",
                type="primary", use_container_width=True,
                on_click=open_contract_from_monitor,
                args=(str(selected["assetcode"]), str(selected["secid"])),
            )

    st.caption(
        "WATCH/CRITICAL определяются выбранным пользователем процентным порогом до LOW/HIGH. "
        "↔ CENTER означает одинаковое расстояние до LOW и HIGH на отображаемой точности. "
        "Источник цены показывает фактически использованное поле MOEX ISS. "
        "Параметры short/collateral вынесены из Risk Radar в детальный обзор выбранного базисного актива."
    )
    st.stop()

selector_contract_col = None
if active_page == "Обзор":
    selector_asset_col, selector_contract_col = st.columns([1, 1.25], gap="large")
    with selector_asset_col:
        assetcode = st.selectbox(
            "Базисный актив",
            options=assetcodes,
            key="selected_assetcode",
            format_func=lambda code: asset_labels.get(code, code),
            help="Параметры из разных источников объединяются по assetcode.",
        )
else:
    assetcode = st.session_state["selected_assetcode"]
market_row = latest_row(market_df, assetcode)
static_row = latest_row(static_df, assetcode)
offdays_row = latest_row(offdays_df, assetcode)
extra_row = latest_row(extra_df, assetcode)
collateral_row = lookup_collateral(assetcode, security_collateral_df, asset_collateral_df)

# Стоимость и абсолютные лимиты зависят уже не только от assetcode, но и от
# конкретного срока исполнения. По умолчанию выбираем ближайший активный
# контракт с доступной ценой, но пользователь может переключить серию.
contract_rows = contracts_for_asset(contracts_df, assetcode)
contract_row: pd.Series | None = None
selected_contract = ""
if not contract_rows.empty and "secid" in contract_rows.columns:
    contract_options = contract_rows["secid"].dropna().astype(str).tolist()
    contract_labels: dict[str, str] = {}
    for _, candidate in contract_rows.iterrows():
        secid = cell_text(candidate.get("secid"))
        if not secid:
            continue
        shortname = cell_text(candidate.get("shortname")) or secid
        expiry = candidate.get("lasttradedate")
        expiry_text = (
            pd.Timestamp(expiry).strftime("%d.%m.%Y")
            if expiry is not None and not pd.isna(expiry)
            else "дата не указана"
        )
        contract_labels[secid] = f"{shortname} · {secid} · последний торговый день {expiry_text}"

    contract_key = f"selected_contract_{assetcode}"
    if contract_key not in st.session_state or st.session_state[contract_key] not in contract_options:
        st.session_state[contract_key] = contract_options[0]
    if active_page == "Обзор" and selector_contract_col is not None:
        with selector_contract_col:
            selected_contract = st.selectbox(
                "Фьючерсный контракт",
                options=contract_options,
                key=contract_key,
                format_func=lambda secid: contract_labels.get(secid, secid),
                help=(
                    "Риск-параметры задаются на уровне базисного актива, а цена, стоимость и абсолютные "
                    "границы зависят от выбранной серии фьючерса."
                ),
            )
    else:
        selected_contract = st.session_state[contract_key]
    selected_rows = contract_rows[contract_rows["secid"].astype(str) == selected_contract]
    if not selected_rows.empty:
        contract_row = selected_rows.iloc[0]
else:
    if active_page == "Обзор":
        st.info(
            "Для этого assetcode не получен список активных фьючерсов из ISS. "
            "Риск-параметры остаются доступны, а блок стоимости будет показан без значений."
        )

actual_at = newest_timestamp(market_row, static_row, extra_row)
actual_at_text = (
    actual_at.strftime("%d.%m.%Y %H:%M:%S")
    if actual_at is not None
    else "нет отметки времени"
)
contract_time = contract_row.get("systime") if contract_row is not None else None
contract_time_text = (
    pd.Timestamp(contract_time).strftime("%d.%m.%Y %H:%M:%S")
    if contract_time is not None and not pd.isna(contract_time)
    else "нет отметки времени"
)
contract_name = (
    cell_text(contract_row.get("shortname")) or selected_contract
    if contract_row is not None
    else "контракт не выбран"
)
dashboard_check_at = datetime.now(ZoneInfo("Europe/Moscow"))
dashboard_special_rows = active_special_parameters(
    special_calendar_df, assetcode, dashboard_check_at
)
if not dashboard_special_rows.empty:
    mode_class = "special"
    mode_label = "SPECIAL NCC MODE"
    active_event = cell_text(dashboard_special_rows.iloc[-1].get("event_name"))
    active_group = cell_text(dashboard_special_rows.iloc[-1].get("market_group"))
    mode_detail = " · ".join(part for part in (active_group, active_event) if part)
else:
    mode_class = "normal"
    mode_label = "STANDARD MODE"
    mode_detail = "Стандартный режим НКЦ"

if active_page == "Обзор":
    source_badge_class = "" if source_all_live else ("error" if source_error else "warn")
    source_badge_label = "DATA LIVE" if source_all_live else ("DATA ERROR" if source_error else "DATA READY")
    st.markdown(
        f'<div class="selection-compact"><div class="selection-main">'
        f'<div class="selection-title">{html.escape(asset_labels.get(assetcode, assetcode))} · {html.escape(contract_name)}</div>'
        f'<div class="selection-sub">{html.escape(selected_contract)}{(" · " + html.escape(mode_detail)) if mode_detail else ""}</div></div>'
        f'<div class="selection-side"><div style="display:flex;gap:.35rem;justify-content:flex-end;align-items:center">'
        f'<span class="source-mini-badge {source_badge_class}"><span class="source-mini-dot"></span>{html.escape(source_badge_label)}</span>'
        f'<span class="mode-badge {mode_class}">{html.escape(mode_label)}</span></div>'
        f'<div class="selection-time">Риск: {html.escape(actual_at_text)}<br>Рынок: {html.escape(contract_time_text)}</div></div></div>',
        unsafe_allow_html=True,
    )
mr1 = market_row.get("mr1") if market_row is not None else None
mr2 = market_row.get("mr2") if market_row is not None else None
mr3 = market_row.get("mr3") if market_row is not None else None
lk1 = market_row.get("lk1") if market_row is not None else None
lk2 = market_row.get("lk2") if market_row is not None else None
standard_total_shifts = static_row.get("autoshiftnummr") if static_row is not None else None
standard_evening_shifts = extra_row.get("autoshiftnummrevg") if extra_row is not None else None
standard_evening_fut_mon_time = extra_row.get("futmontimeevg") if extra_row is not None else None
evening_ir_shifts = extra_row.get("autoshiftnumirevg") if extra_row is not None else None
evening_cs_mon_time = extra_row.get("csmontimeevg") if extra_row is not None else None
standard_fut_mon_time = static_row.get("futmontime") if static_row is not None else None
standard_range_fut = static_row.get("rangefut") if static_row is not None else None

total_shifts, total_shifts_override = special_override(
    dashboard_special_rows, "AutoShiftNumMR", standard_total_shifts
)
evening_shifts, evening_shifts_override = special_override(
    dashboard_special_rows, "AutoShiftNumMREvg", standard_evening_shifts
)
fut_mon_time, fut_mon_time_override = special_override(
    dashboard_special_rows, "FutMonTimeDay", standard_fut_mon_time
)
range_fut, range_fut_override = special_override(
    dashboard_special_rows, "RangeFut", standard_range_fut
)

# Morning currency bounds use the same official parameter that NCC publishes
# for weekend trading. The dedicated OffDays XLSX is the primary source.
weekend_shift = None
if offdays_row is not None and not is_missing(offdays_row.get("offdaystradingpricerangeshift")):
    weekend_shift = offdays_row.get("offdaystradingpricerangeshift")
elif extra_row is not None and not is_missing(extra_row.get("offdaystradingpricerangeshift")):
    weekend_shift = extra_row.get("offdaystradingpricerangeshift")
price_ref = reference_price(contract_row)
current_contract_value, current_value_note = contract_value_rub(contract_row, price_ref)
price_decimals = None
if contract_row is not None and not is_missing(contract_row.get("decimals")):
    price_decimals = max(0, int(float(contract_row.get("decimals"))))

current_low_quote = contract_row.get("lowlimit") if contract_row is not None else None
current_high_quote = contract_row.get("highlimit") if contract_row is not None else None
current_low_rub = (
    price_to_rub(current_low_quote, contract_row.get("minstep"), contract_row.get("stepprice"))
    if contract_row is not None
    else None
)
current_high_rub = (
    price_to_rub(current_high_quote, contract_row.get("minstep"), contract_row.get("stepprice"))
    if contract_row is not None
    else None
)

lot_volume = contract_row.get("lotvolume") if contract_row is not None else None
last_rub_value = contract_row.get("last_rub") if contract_row is not None else None
initial_margin = contract_row.get("initialmargin") if contract_row is not None else None
lk1_rub_equivalent = concentration_limit_to_rub(lk1, lot_volume, last_rub_value)
lk2_rub_equivalent = concentration_limit_to_rub(lk2, lot_volume, last_rub_value)

asset_title = (
    cell_text(market_row.get("title")) if market_row is not None else ""
) or (
    cell_text(offdays_row.get("title")) if offdays_row is not None else ""
)
currency_future = is_currency_future(assetcode, asset_title)

min_step = contract_row.get("minstep") if contract_row is not None else None
step_price = contract_row.get("stepprice") if contract_row is not None else None
rub_per_quote_unit = None
if not is_missing(min_step) and not is_missing(step_price):
    try:
        if float(min_step) > 0:
            rub_per_quote_unit = float(step_price) / float(min_step)
    except (TypeError, ValueError, ZeroDivisionError):
        rub_per_quote_unit = None

current_width_quote = (
    float(current_high_quote) - float(current_low_quote)
    if not is_missing(current_low_quote) and not is_missing(current_high_quote)
    else None
)
current_width_rub = (
    float(current_high_rub) - float(current_low_rub)
    if not is_missing(current_low_rub) and not is_missing(current_high_rub)
    else None
)

morning_ref = morning_reference_price(contract_row)
morning_estimate = None
morning_low = None
morning_high = None
morning_low_rub = None
morning_high_rub = None
morning_offdays_low = None
morning_offdays_high = None
if currency_future:
    morning_estimate = estimate_morning_limits(
        current_low=current_low_quote,
        current_high=current_high_quote,
        reference_price=morning_ref.value,
        offdays_shift=weekend_shift,
    )
    morning_low = morning_estimate.effective_low
    morning_high = morning_estimate.effective_high
    morning_offdays_low = morning_estimate.offdays_low
    morning_offdays_high = morning_estimate.offdays_high
    if contract_row is not None:
        morning_low_rub = price_to_rub(
            morning_low, contract_row.get("minstep"), contract_row.get("stepprice")
        )
        morning_high_rub = price_to_rub(
            morning_high, contract_row.get("minstep"), contract_row.get("stepprice")
        )

lk1_contracts = contracts_at_limit(lk1, lot_volume)
lk2_contracts = contracts_at_limit(lk2, lot_volume)


if active_page == "Обзор":
    # Presentation summary: the six values that should be readable in a few seconds.
    st.markdown(
        '<div class="section-head"><div class="section-kicker">Ключевой срез</div>'
        '<div class="section-title">Риск-профиль выбранного контракта</div>'
        '<div class="section-subtitle">Цена, стоимость, концентрационные пороги и текущие ценовые границы.</div></div>',
        unsafe_allow_html=True,
    )
    k1, k2, k3, k4, k5, k6 = st.columns(6, gap="small")
    with k1:
        kpi_card("Текущая цена", fmt_number(price_ref.value, price_decimals), price_ref.field.upper() if price_ref.field else "PRICE", "market")
    with k2:
        kpi_card("Стоимость контракта", fmt_compact_rub(current_contract_value), "Рублёвый ориентир за 1 контракт", "market")
    with k3:
        kpi_card("Порог LK1", f"{fmt_number(lk1_contracts, 0)} контр.", f"{fmt_integer(lk1)} ед. БА", "limit")
    with k4:
        kpi_card("Порог LK2", f"{fmt_number(lk2_contracts, 0)} контр.", f"{fmt_integer(lk2)} ед. БА", "limit")
    with k5:
        kpi_card("LOWLIMIT", fmt_number(current_low_quote, price_decimals), fmt_rub(current_low_rub), "risk")
    with k6:
        kpi_card("HIGHLIMIT", fmt_number(current_high_quote, price_decimals), fmt_rub(current_high_rub), "risk")

    if collateral_row is not None:
        source_kind = cell_text(collateral_row.get("source_kind"))
        collateral_source_status = security_collateral_status if source_kind == "security" else asset_collateral_status
        short_ban = collateral_row.get("short_sale_ban")
        collateral_accept = collateral_row.get("collateral_accepted")
        short_ban_text = yes_no_text(short_ban)
        collateral_text = yes_no_text(collateral_accept)
        st.markdown(
            '<div class="section-head"><div class="section-kicker">Базисный актив · другие рынки НКЦ</div>'
            '<div class="section-title">Параметры базисного актива на других рынках НКЦ</div>'
            '<div class="section-subtitle">Короткие продажи и приём в обеспечение относятся к самому базисному активу на фондовом / валютном рынке НКЦ, а не к фьючерсной серии.</div></div>',
            unsafe_allow_html=True,
        )
        parameter_strip([
            ("SHORT_SALE_BAN", "Запрет коротких продаж", short_ban_text, "официальный признак, если опубликован"),
            ("SHORT_LIMIT", "Лимит коротких продаж", fmt_number(collateral_row.get("short_sale_limit"), 2), "единицы соответствующего актива"),
            ("COLLATERAL", "Принимается в обеспечение", collateral_text, "параметр базисного актива"),
            ("COLLATERAL_LIMIT", "Лимит приёма", (fmt_number(collateral_row.get("collateral_limit_pct"), 2) + "%") if not is_missing(collateral_row.get("collateral_limit_pct")) else "—", f"источник: {collateral_source_status.state}"),
        ])

    st.markdown(
        '<div class="section-head"><div class="section-kicker">Концентрационный риск</div>'
        '<div class="section-title">Ступени MR1–MR3 и пороги LK1–LK2</div></div>',
        unsafe_allow_html=True,
    )
    if presentation_mode:
        concentration_ladder(mr1, mr2, mr3, lk1_contracts, lk2_contracts, lk1_rub_equivalent, lk2_rub_equivalent)
    else:
        st.caption("Повышенная ставка применяется только к части позиции, превысившей соответствующий порог.")
        concentration_scale(mr1, mr2, mr3, lk1_contracts, lk2_contracts)
        lk_a, lk_b = st.columns(2)
        with lk_a:
            metric_card(
                "LK1 · номинальный эквивалент",
                fmt_compact_rub(lk1_rub_equivalent),
                f"{fmt_integer(lk1)} ед. БА ≈ {fmt_number(lk1_contracts, 0)} контрактов",
                code="LK1 → CONTRACTS → RUB", tone="blue", compact=True,
            )
        with lk_b:
            metric_card(
                "LK2 · номинальный эквивалент",
                fmt_compact_rub(lk2_rub_equivalent),
                f"{fmt_integer(lk2)} ед. БА ≈ {fmt_number(lk2_contracts, 0)} контрактов",
                code="LK2 → CONTRACTS → RUB", tone="blue", compact=True,
            )

    st.markdown(
        '<div class="section-head"><div class="section-kicker">Экономика контракта</div>'
        '<div class="section-title">Как котировка превращается в рублёвую стоимость</div></div>',
        unsafe_allow_html=True,
    )
    parameter_strip([
        ("LOTVOLUME", "Размер лота", fmt_number(lot_volume, 4), "ед. БА / контракт"),
        ("MINSTEP", "Минимальный шаг", fmt_number(min_step, 8), "изменение котировки"),
        ("STEPPRICE", "Цена шага", fmt_rub(step_price), "₽ за MINSTEP"),
        ("RUB_PER_POINT", "1 пункт котировки", fmt_rub(rub_per_quote_unit), "STEPPRICE ÷ MINSTEP"),
        ("LAST_RUB", "Стоимость контракта", fmt_rub(current_contract_value), current_value_note),
    ])

    missing: list[str] = []
    if any(is_missing(v) for v in (mr1, mr2, mr3, lk1, lk2)):
        missing.append("MR1–MR3 / LK1–LK2")
    if is_missing(total_shifts) and total_shifts_override is None:
        missing.append("AutoShiftNumMR")
    if is_missing(evening_shifts) and evening_shifts_override is None:
        missing.append("AutoShiftNumMREvg")
    if is_missing(standard_evening_fut_mon_time):
        missing.append("FutMonTimeEvg")
    if is_missing(fut_mon_time) and fut_mon_time_override is None:
        missing.append("FutMonTimeDay")
    if is_missing(range_fut) and range_fut_override is None:
        missing.append("RangeFut")
    if is_missing(weekend_shift):
        missing.append("OffDaysTradingPriceRangeShift")
    if missing:
        st.warning(
            "Для выбранного актива не получены: " + ", ".join(missing) + ". "
            "Карточки со знаком «—» не заменяются предположениями."
        )

    if not presentation_mode:
        with st.expander("Технические значения: все используемые параметры", expanded=False):
            technical_rows: list[dict[str, str]] = []

            def add_technical_row(
                section: str,
                parameter: str,
                value: object,
                unit: str,
                source: str,
                value_type: str = "Исходный",
                formatter=None,
            ) -> None:
                if formatter is not None:
                    display_value = formatter(value)
                elif value is None or pd.isna(value):
                    display_value = "—"
                elif isinstance(value, pd.Timestamp):
                    display_value = value.strftime("%d.%m.%Y %H:%M:%S")
                else:
                    display_value = str(value)
                technical_rows.append(
                    {
                        "Раздел": section,
                        "Параметр": parameter,
                        "Значение": display_value,
                        "Единица": unit,
                        "Источник": source,
                        "Тип": value_type,
                    }
                )

            # Выбор пользователя и идентификаторы.
            add_technical_row("Выбор", "assetcode", assetcode, "код", "Выбор пользователя", "Служебный")
            add_technical_row("Выбор", "SECID", selected_contract, "код", "MOEX ISS · контракт", "Служебный")
            add_technical_row("Выбор", "SHORTNAME", contract_name, "текст", "MOEX ISS · контракт", "Служебный")

            # Рыночный риск и концентрация.
            add_technical_row("Риск", "MR1", mr1, "доля", "MR/LK", formatter=lambda v: fmt_number(v, 6))
            add_technical_row("Риск", "MR2", mr2, "доля", "MR/LK", formatter=lambda v: fmt_number(v, 6))
            add_technical_row("Риск", "MR3", mr3, "доля", "MR/LK", formatter=lambda v: fmt_number(v, 6))
            add_technical_row("Концентрация", "LK1", lk1, "ед. БА", "MR/LK", formatter=fmt_integer)
            add_technical_row("Концентрация", "LK2", lk2, "ед. БА", "MR/LK", formatter=fmt_integer)

            # Торговые ограничения. Активный календарь имеет приоритет над стандартными значениями.
            add_technical_row(
                "Ограничения", "AutoShiftNumMR", total_shifts, "сдвигов",
                parameter_source(total_shifts_override, "Статические параметры"),
                "Специальный" if total_shifts_override is not None else "Исходный",
                formatter=fmt_integer,
            )
            add_technical_row(
                "Ограничения", "AutoShiftNumMREvg", evening_shifts, "сдвигов",
                parameter_source(evening_shifts_override, "Официальный XLSX НКЦ · вечерняя сессия"),
                "Специальный" if evening_shifts_override is not None else "Исходный",
                formatter=fmt_integer,
            )
            add_technical_row(
                "Ограничения", "FutMonTimeEvg", standard_evening_fut_mon_time, "сек.",
                "Официальный XLSX НКЦ · вечерняя сессия", formatter=fmt_integer,
            )
            add_technical_row(
                "Ограничения", "AutoShiftNumIREvg", evening_ir_shifts, "сдвигов",
                "Официальный XLSX НКЦ · вечерняя сессия", formatter=fmt_integer,
            )
            add_technical_row(
                "Ограничения", "CSMonTimeEvg", evening_cs_mon_time, "сек.",
                "Официальный XLSX НКЦ · вечерняя сессия", formatter=fmt_integer,
            )
            add_technical_row(
                "Ограничения", "FutMonTimeDay", fut_mon_time, "сек.",
                parameter_source(fut_mon_time_override, "Статические параметры"),
                "Специальный" if fut_mon_time_override is not None else "Исходный",
                formatter=fmt_integer,
            )
            add_technical_row(
                "Ограничения", "RangeFut", range_fut, "доля",
                parameter_source(range_fut_override, "Статические параметры"),
                "Специальный" if range_fut_override is not None else "Исходный",
                formatter=lambda v: fmt_number(v, 6),
            )
            add_technical_row(
                "Ограничения",
                "OffDaysTradingPriceRangeShift",
                weekend_shift,
                "доля",
                "XLSX НКЦ",
                formatter=lambda v: fmt_number(v, 6),
            )

            # Поля выбранной серии, участвующие в выборе цены и расчётах.
            contract_values = contract_row if contract_row is not None else pd.Series(dtype=object)
            add_technical_row("Контракт", "LAST", contract_values.get("last"), "котировка", "MOEX ISS", formatter=lambda v: fmt_number(v, price_decimals))
            add_technical_row("Контракт", "SETTLEPRICE", contract_values.get("settleprice"), "котировка", "MOEX ISS", formatter=lambda v: fmt_number(v, price_decimals))
            add_technical_row("Контракт", "LASTSETTLEPRICE", contract_values.get("lastsettleprice"), "котировка", "MOEX ISS", formatter=lambda v: fmt_number(v, price_decimals))
            add_technical_row("Контракт", "PREVSETTLEPRICE", contract_values.get("prevsettleprice"), "котировка", "MOEX ISS", formatter=lambda v: fmt_number(v, price_decimals))
            add_technical_row("Контракт", "REFERENCE_PRICE_FIELD", price_ref.field.upper() if price_ref.field else None, "поле", "Логика дашборда", "Служебный")
            add_technical_row("Контракт", "REFERENCE_PRICE", price_ref.value, "котировка", "Логика дашборда", "Расчётный", formatter=lambda v: fmt_number(v, price_decimals))
            add_technical_row("Контракт", "LAST_RUB", contract_values.get("last_rub"), "₽/контракт", "MOEX ISS", formatter=fmt_rub)
            add_technical_row("Контракт", "LOTVOLUME", lot_volume, "ед. БА/контракт", "MOEX ISS", formatter=lambda v: fmt_number(v, 6))
            add_technical_row("Контракт", "MINSTEP", contract_values.get("minstep"), "котировка", "MOEX ISS", formatter=lambda v: fmt_number(v, 8))
            add_technical_row("Контракт", "STEPPRICE", contract_values.get("stepprice"), "₽/шаг", "MOEX ISS", formatter=lambda v: fmt_number(v, 8))
            add_technical_row("Контракт", "LOWLIMIT", current_low_quote, "котировка", "MOEX ISS", formatter=lambda v: fmt_number(v, price_decimals))
            add_technical_row("Контракт", "HIGHLIMIT", current_high_quote, "котировка", "MOEX ISS", formatter=lambda v: fmt_number(v, price_decimals))
            add_technical_row("Расчёт", "CURRENT_LIMIT_WIDTH", current_width_quote, "котировка", "HIGHLIMIT − LOWLIMIT", "Расчётный", lambda v: fmt_number(v, price_decimals))
            if currency_future:
                add_technical_row("Утренняя сессия", "MORNING_AUTO_SHIFT_NUM", 0, "сдвигов", "Методика НКЦ", "Нормативный", fmt_integer)
                add_technical_row("Утренняя сессия", "MORNING_REFERENCE_PRICE", morning_ref.value, "котировка", morning_ref.label, "Прокси", formatter=lambda v: fmt_number(v, price_decimals))
                add_technical_row("Утренняя сессия", "MORNING_OFFDAYS_SHIFT", weekend_shift, "доля", "XLSX НКЦ · выходные дни", formatter=lambda v: fmt_number(v, 6))
                add_technical_row("Утренняя сессия", "MORNING_OFFDAYS_LOW", morning_offdays_low, "котировка", "Pproxy − s×|Pproxy|", "Расчётный", lambda v: fmt_number(v, price_decimals))
                add_technical_row("Утренняя сессия", "MORNING_OFFDAYS_HIGH", morning_offdays_high, "котировка", "Pproxy + s×|Pproxy|", "Расчётный", lambda v: fmt_number(v, price_decimals))
                add_technical_row("Утренняя сессия", "MORNING_LOWLIMIT_ESTIMATE", morning_low, "котировка", "max(LOWLIMIT, MORNING_OFFDAYS_LOW)", "Расчётный", lambda v: fmt_number(v, price_decimals))
                add_technical_row("Утренняя сессия", "MORNING_HIGHLIMIT_ESTIMATE", morning_high, "котировка", "min(HIGHLIMIT, MORNING_OFFDAYS_HIGH)", "Расчётный", lambda v: fmt_number(v, price_decimals))
                add_technical_row("Утренняя сессия", "MORNING_LOWLIMIT_RUB", morning_low_rub, "₽/контракт", "Расчёт дашборда", "Расчётный", fmt_rub)
                add_technical_row("Утренняя сессия", "MORNING_HIGHLIMIT_RUB", morning_high_rub, "₽/контракт", "Расчёт дашборда", "Расчётный", fmt_rub)
            add_technical_row("Контракт", "DECIMALS", contract_values.get("decimals"), "знаков", "MOEX ISS", formatter=fmt_integer)
            add_technical_row("Контракт", "LASTTRADEDATE", contract_values.get("lasttradedate"), "дата", "MOEX ISS")
            add_technical_row("Контракт", "OPENPOSITION", contract_values.get("openposition"), "контрактов", "MOEX ISS", formatter=fmt_integer)
            add_technical_row("Контракт", "SYSTIME", contract_values.get("systime"), "дата/время", "MOEX ISS")
            add_technical_row("Контракт", "UPDATETIME", contract_values.get("updatetime"), "время", "MOEX ISS")

            # Значения, рассчитанные самим дашбордом.
            add_technical_row("Расчёт", "CONTRACT_VALUE_RUB", current_contract_value, "₽/контракт", "Расчёт дашборда", "Расчётный", fmt_rub)
            add_technical_row("Расчёт", "LOWLIMIT_RUB", current_low_rub, "₽/контракт", "Расчёт дашборда", "Расчётный", fmt_rub)
            add_technical_row("Расчёт", "HIGHLIMIT_RUB", current_high_rub, "₽/контракт", "Расчёт дашборда", "Расчётный", fmt_rub)
            add_technical_row("Расчёт", "LK1_RUB_EQUIVALENT", lk1_rub_equivalent, "₽", "LK1 ÷ LOTVOLUME × LAST_RUB", "Расчётный", fmt_rub)
            add_technical_row("Расчёт", "LK2_RUB_EQUIVALENT", lk2_rub_equivalent, "₽", "LK2 ÷ LOTVOLUME × LAST_RUB", "Расчётный", fmt_rub)

            technical_df = pd.DataFrame(technical_rows)
            st.dataframe(
                technical_df,
                use_container_width=True,
                hide_index=True,
                height=min(1200, 36 * len(technical_df) + 38),
                column_config={
                    "Раздел": st.column_config.TextColumn(width="small"),
                    "Параметр": st.column_config.TextColumn(width="medium"),
                    "Значение": st.column_config.TextColumn(width="medium"),
                    "Единица": st.column_config.TextColumn(width="small"),
                    "Источник": st.column_config.TextColumn(width="medium"),
                    "Тип": st.column_config.TextColumn(width="small"),
                },
            )

if active_page == "Границы":
    st.markdown(
        '<div class="section-head"><div class="section-kicker">Ценовой риск</div>'
        '<div class="section-title">Границы и механизм автоматических раздвижек</div>'
        '<div class="section-subtitle">Официальные границы выбранной серии и параметры их мониторинга. Специальный календарь автоматически имеет приоритет над стандартными настройками.</div></div>',
        unsafe_allow_html=True,
    )

    price_corridor(current_low_quote, price_ref.value, current_high_quote, current_width_quote, price_decimals)
    if not presentation_mode:
        parameter_strip([
            ("LOWLIMIT", "Нижняя граница", fmt_number(current_low_quote, price_decimals), fmt_rub(current_low_rub)),
            ("HIGH − LOW", "Ширина коридора", fmt_number(current_width_quote, price_decimals), fmt_rub(current_width_rub)),
            ("HIGHLIMIT", "Верхняя граница", fmt_number(current_high_quote, price_decimals), fmt_rub(current_high_rub)),
        ])

    st.markdown(
        '<div class="section-head"><div class="section-kicker">Автоматический контроль</div>'
        '<div class="section-title">Мониторинг и раздвижки</div></div>',
        unsafe_allow_html=True,
    )
    if presentation_mode:
        monitoring_flow(range_fut, fut_mon_time, total_shifts, evening_shifts, standard_evening_fut_mon_time)
    parameter_strip([
        (
            "AutoShiftNumMR" + (" · SPECIAL" if total_shifts_override is not None else ""),
            "Общий лимит сдвигов", fmt_integer(total_shifts),
            special_parameter_note(total_shifts_override, "основная + вечерняя сессии"),
        ),
        (
            "FutMonTimeDay" + (" · SPECIAL" if fut_mon_time_override is not None else ""),
            "Мониторинг днём", fmt_seconds(fut_mon_time),
            special_parameter_note(fut_mon_time_override, "условие перед раздвижкой"),
        ),
        (
            "AutoShiftNumMREvg" + (" · SPECIAL" if evening_shifts_override is not None else ""),
            "Лимит вечером", fmt_integer(evening_shifts),
            special_parameter_note(evening_shifts_override, evening_limit_note(extra_row, extra_status)),
        ),
        (
            "FutMonTimeEvg", "Мониторинг вечером", fmt_seconds(standard_evening_fut_mon_time),
            "официальный XLSX НКЦ · вечерняя сессия",
        ),
        (
            "RangeFut" + (" · SPECIAL" if range_fut_override is not None else ""),
            "Ширина контрольного коридора", fmt_rate(range_fut),
            special_parameter_note(range_fut_override, "официальный RangeFut · ширина контрольного коридора"),
        ),
    ])

    if not dashboard_special_rows.empty:
        st.warning(
            "Для выбранного базисного актива сейчас действует специальный режим НКЦ. "
            "Карточки с пометкой SPECIAL уже показывают значения из календаря специальных риск-параметров."
        )

    if currency_future:
        st.markdown(
            '<div class="section-head"><div class="section-kicker">Утренняя сессия · валютные фьючерсы</div>'
            '<div class="section-title">Аналитическая оценка утренних границ</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="estimate-box"><span class="estimate-badge">ANALYTICAL ESTIMATE</span>'
            '<div class="estimate-title">OffDaysTradingPriceRangeShift и эффективный утренний коридор</div>'
            '<div class="estimate-text">Официальный параметр НКЦ применяется к явно подписанной proxy-цене, потому что отдельное публичное поле Pmarket23:50 в ISS отсутствует.</div></div>',
            unsafe_allow_html=True,
        )
        mo1, mo2, mo3, mo4 = st.columns(4)
        with mo1:
            metric_card("Shift на сторону", fmt_rate(weekend_shift), "Параметр выходных торгов НКЦ", code="OffDaysTradingPriceRangeShift", tone="green", compact=True)
        with mo2:
            metric_card("Proxy-цена", fmt_number(morning_ref.value, price_decimals), morning_ref.label, code="P PROXY", tone="neutral", compact=True)
        with mo3:
            metric_card("Morning LOW", fmt_number(morning_low, price_decimals), fmt_rub(morning_low_rub), code="max(LOW, L_hol)", tone="amber", compact=True)
        with mo4:
            metric_card("Morning HIGH", fmt_number(morning_high, price_decimals), fmt_rub(morning_high_rub), code="min(HIGH, H_hol)", tone="amber", compact=True)
        if morning_estimate is not None and morning_estimate.error:
            st.warning("Утренние границы не рассчитаны: " + morning_estimate.error + ".")
        else:
            st.caption(
                f"Выходной коридор по proxy: {fmt_number(morning_offdays_low, price_decimals)} — "
                f"{fmt_number(morning_offdays_high, price_decimals)}. Автоматические сдвиги в утреннюю дополнительную сессию: 0."
            )
    elif not presentation_mode:
        st.markdown(
            '<div class="section-head"><div class="section-kicker">Торги в выходные дни</div>'
            '<div class="section-title">OffDaysTradingPriceRangeShift</div></div>',
            unsafe_allow_html=True,
        )
        off_col, _ = st.columns([1, 2])
        with off_col:
            metric_card(
                "Отклонение на одну сторону", fmt_rate(weekend_shift),
                "Доля от |Pmarket23:50| в каждую сторону", code="OffDaysTradingPriceRangeShift", tone="green"
            )
        st.caption("Утренний расчёт эффективных границ показывается только для валютных фьючерсов.")


if active_page == "Спецрежимы НКЦ":
    st.markdown(
        '<div class="section-head"><div class="section-kicker">Зарубежные площадки / календарь</div>'
        '<div class="section-title">Специальные режимы НКЦ</div>'
        '<div class="section-subtitle">Проверка по коду БА и моменту времени: действует ли специальный режим раздвижек и какие настройки применяются.</div></div>',
        unsafe_allow_html=True,
    )

    calendar_codes = calendar_assetcodes(special_calendar_df)
    special_options = sorted(set(assetcodes).union(calendar_codes), key=str.upper)
    if not special_options:
        st.error("Календарь и основные источники не содержат кодов базисных активов.")
    else:
        default_special_code = assetcode if assetcode in special_options else special_options[0]
        if st.session_state.get("special_assetcode") not in special_options:
            st.session_state["special_assetcode"] = default_special_code

        now_msk = datetime.now(ZoneInfo("Europe/Moscow"))
        filter_col, date_col, time_col = st.columns([1.5, 1, 1])
        with filter_col:
            special_assetcode = st.selectbox(
                "Код базисного актива",
                options=special_options,
                key="special_assetcode",
                format_func=lambda code: asset_labels.get(code, code),
            )
        with date_col:
            special_date = st.date_input(
                "Дата проверки",
                value=now_msk.date(),
                key="special_check_date",
            )
        with time_col:
            special_time = st.time_input(
                "Время, МСК",
                value=now_msk.time().replace(second=0, microsecond=0),
                key="special_check_time",
            )

        check_at = datetime.combine(special_date, special_time, tzinfo=ZoneInfo("Europe/Moscow"))
        active_rows = active_special_parameters(special_calendar_df, special_assetcode, check_at)
        asset_in_calendar = special_assetcode in calendar_codes

        state_col, count_col, period_col = st.columns(3)
        with state_col:
            metric_card(
                "Специальный режим действует",
                "ДА" if not active_rows.empty else "НЕТ",
                "На выбранный момент времени",
                code="SPECIAL RISK",
                tone="amber" if not active_rows.empty else "green",
                compact=True,
            )
        with count_col:
            metric_card(
                "Активных настроек",
                fmt_integer(active_rows["parameter"].nunique()) if not active_rows.empty else "0",
                "Настройки управления ценовыми границами",
                code="PARAMETERS",
                tone="blue",
                compact=True,
            )
        with period_col:
            if not active_rows.empty:
                start_value = active_rows["start_at"].dropna().min()
                end_value = active_rows["end_at"].dropna().max()
                period_value = (
                    f"{pd.Timestamp(start_value).strftime('%d.%m %H:%M')} — "
                    f"{pd.Timestamp(end_value).strftime('%d.%m %H:%M')}"
                    if not pd.isna(start_value) and not pd.isna(end_value)
                    else "Не указан"
                )
            else:
                period_value = "—"
            metric_card(
                "Период действия",
                period_value,
                "Время Московское",
                code="VALIDITY",
                tone="neutral",
                compact=True,
            )

        if special_calendar_df.empty:
            st.warning(
                "Официальный XLSX не загрузился. Откройте «Источники данных и ручная загрузка» и загрузите календарь вручную."
            )
        elif not asset_in_calendar:
            st.success(
                f"Для {special_assetcode} в календаре НКЦ на 2026 год специальные режимы не запланированы."
            )
        elif active_rows.empty:
            st.success(
                f"Для {special_assetcode} на {check_at.strftime('%d.%m.%Y %H:%M')} МСК специальный режим не действует."
            )
        else:
            active_display = active_rows.copy()
            active_display["Параметр"] = active_display["parameter"]
            active_display["Что меняется"] = active_display["parameter_title"]
            active_display["Значение"] = active_display["value_raw"]
            active_display["Единица"] = active_display["unit"]
            active_display["Начало"] = active_display["start_at"].dt.strftime("%d.%m.%Y %H:%M")
            active_display["Окончание"] = active_display["end_at"].dt.strftime("%d.%m.%Y %H:%M")
            active_display["Событие / площадка"] = active_display.get("event_name", "")
            active_display["Зарубежная площадка"] = active_display.get("market_group", "")
            st.dataframe(
                active_display[["Событие / площадка", "Зарубежная площадка", "Параметр", "Что меняется", "Значение", "Единица", "Начало", "Окончание"]],
                use_container_width=True,
                hide_index=True,
            )

        future_rows = future_special_periods(
            special_calendar_df, special_assetcode, check_at, limit=30
        )
        if not future_rows.empty:
            with st.expander("Ближайшие будущие периоды для выбранного БА", expanded=False):
                future_display = future_rows.copy()
                future_display["Дата праздника"] = future_display.get("holiday_date", pd.Series(index=future_display.index, dtype="datetime64[ns]")).dt.strftime("%d.%m.%Y")
                future_display["Событие"] = future_display.get("event_name", "")
                future_display["Начало"] = future_display["start_at"].dt.strftime("%d.%m.%Y %H:%M")
                future_display["Окончание"] = future_display["end_at"].dt.strftime("%d.%m.%Y %H:%M")
                future_display["Параметр"] = future_display["parameter"]
                future_display["Значение"] = future_display["value_raw"]
                st.dataframe(
                    future_display[["Дата праздника", "Событие", "Начало", "Окончание", "Параметр", "Значение", "unit"]].rename(
                        columns={"unit": "Единица"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        with st.expander("Показать полный календарь НКЦ по всем базисным активам", expanded=False):
            prepare_full_calendar = st.checkbox(
                "Подготовить полную таблицу", value=False, key="prepare_full_special_calendar",
                help="Таблица строится только по запросу, чтобы обычные переходы по дашборду были быстрее.",
            )
            calendar_display = calendar_wide_view(special_calendar_df) if prepare_full_calendar else pd.DataFrame()
            if prepare_full_calendar and not calendar_display.empty:
                calendar_display = calendar_display.copy()
                calendar_display["Дата праздника"] = calendar_display.get("holiday_date", pd.Series(index=calendar_display.index, dtype="datetime64[ns]")).dt.strftime("%d.%m.%Y")
                calendar_display["Событие"] = calendar_display.get("event_name", "")
                calendar_display["Зарубежная площадка"] = calendar_display.get("market_group", "")
                calendar_display["Начало"] = calendar_display["start_at"].dt.strftime("%d.%m.%Y %H:%M")
                calendar_display["Окончание"] = calendar_display["end_at"].dt.strftime("%d.%m.%Y %H:%M")
                display_columns = ["Дата праздника", "Событие", "Зарубежная площадка", "Начало", "Окончание", "assetcode", "asset_title"] + [
                    parameter for parameter in PARAMETER_META if parameter in calendar_display.columns
                ]
                st.dataframe(
                    calendar_display[display_columns].rename(
                        columns={"assetcode": "Код БА", "asset_title": "Базисный актив"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=520,
                )
                st.download_button(
                    "Скачать распознанный календарь CSV",
                    data=calendar_display.to_csv(index=False).encode("utf-8-sig"),
                    file_name="special_risk_calendar_normalized.csv",
                    mime="text/csv",
                )
            elif prepare_full_calendar:
                st.caption("Нет распознанных строк календаря.")
            else:
                st.caption("Полная таблица не строится до запроса — это ускоряет обычную работу интерфейса.")

            st.caption(
                "Источник: официальный XLSX НКЦ «Календарь применения специальных риск-параметров в неторговые дни на зарубежных биржах»."
            )


if active_page == "Калькулятор ГО":
    st.markdown(
        '<div class="section-head"><div class="section-kicker">Гарантийное обеспечение</div>'
        '<div class="section-title">Калькулятор ГО по уровням концентрации</div>'
        '<div class="section-subtitle">Повышенная ставка не применяется ко всей позиции: только превышение LK1/LK2 попадает на следующий уровень.</div></div>',
        unsafe_allow_html=True,
    )

    st.caption(
        f"{contract_name or selected_contract or assetcode} · "
        f"LK1 ≈ {fmt_number(lk1_contracts, 0)} контр. · LK2 ≈ {fmt_number(lk2_contracts, 0)} контр. · "
        f"INITIALMARGIN {fmt_rub(initial_margin)}"
    )

    calc_key = f"dedicated_position_contracts_{selected_contract or assetcode}"
    lk1_demo = math.floor(lk1_contracts) if lk1_contracts is not None else None
    lk2_demo = math.floor(lk2_contracts) if lk2_contracts is not None else None

    if presentation_mode:
        input_col, demo_col = st.columns([1.05, 4.2], gap="large")
        with input_col:
            calculator_position_contracts = st.number_input(
                "Количество контрактов", min_value=0, value=1, step=1, key=calc_key,
                help="Укажите количество контрактов выбранной серии.",
            )
        with demo_col:
            st.caption("Быстрый переход через концентрационные пороги")
            q1, q2, q3, q4, q5 = st.columns(5)
            with q1:
                st.button("1", key=f"q1_{calc_key}", use_container_width=True, on_click=set_session_number, args=(calc_key, 1), help="1 контракт")
            with q2:
                st.button("LK1", key=f"qlk1_{calc_key}", use_container_width=True, disabled=lk1_demo is None, on_click=set_session_number if lk1_demo is not None else None, args=(calc_key, lk1_demo or 0))
            with q3:
                st.button("LK1 + 1", key=f"qlk1p_{calc_key}", use_container_width=True, disabled=lk1_demo is None, on_click=set_session_number if lk1_demo is not None else None, args=(calc_key, (lk1_demo or 0) + 1))
            with q4:
                st.button("LK2", key=f"qlk2_{calc_key}", use_container_width=True, disabled=lk2_demo is None, on_click=set_session_number if lk2_demo is not None else None, args=(calc_key, lk2_demo or 0))
            with q5:
                st.button("LK2 + 1", key=f"qlk2p_{calc_key}", use_container_width=True, disabled=lk2_demo is None, on_click=set_session_number if lk2_demo is not None else None, args=(calc_key, (lk2_demo or 0) + 1))
    else:
        calculator_position_contracts = st.number_input(
            "Количество контрактов", min_value=0, value=1, step=1, key=calc_key,
            help="Укажите количество контрактов выбранной серии.",
        )
        st.caption("Быстрый demo: покажите переход ставки на превышении порога одним кликом.")
        q1, q2, q3, q4, q5 = st.columns(5)
        with q1:
            st.button("1 контракт", key=f"q1_{calc_key}", use_container_width=True, on_click=set_session_number, args=(calc_key, 1))
        with q2:
            st.button("LK1", key=f"qlk1_{calc_key}", use_container_width=True, disabled=lk1_demo is None, on_click=set_session_number if lk1_demo is not None else None, args=(calc_key, lk1_demo or 0))
        with q3:
            st.button("LK1 + 1", key=f"qlk1p_{calc_key}", use_container_width=True, disabled=lk1_demo is None, on_click=set_session_number if lk1_demo is not None else None, args=(calc_key, (lk1_demo or 0) + 1))
        with q4:
            st.button("LK2", key=f"qlk2_{calc_key}", use_container_width=True, disabled=lk2_demo is None, on_click=set_session_number if lk2_demo is not None else None, args=(calc_key, lk2_demo or 0))
        with q5:
            st.button("LK2 + 1", key=f"qlk2p_{calc_key}", use_container_width=True, disabled=lk2_demo is None, on_click=set_session_number if lk2_demo is not None else None, args=(calc_key, (lk2_demo or 0) + 1))

    calculator_position_margin = progressive_position_margin(
        calculator_position_contracts,
        lot_volume,
        lk1,
        lk2,
        mr1,
        mr2,
        mr3,
        initial_margin,
    )
    calculator_level_labels = {0: "Нет позиции", 1: "MR1", 2: "MR2", 3: "MR3"}
    calculator_active_level = calculator_level_labels.get(
        calculator_position_margin.highest_level, "Нет данных"
    )
    calculator_active_tone = {0: "neutral", 1: "green", 2: "amber", 3: "neutral"}.get(
        calculator_position_margin.highest_level, "neutral"
    )

    if presentation_mode and calculator_position_margin.error is None and calculator_position_margin.highest_level >= 2:
        if calculator_position_margin.highest_level == 2:
            lk2_n = _as_float(lk2_contracts)
            next_mr3 = int(lk2_n) + 1 if lk2_n is not None else None
            mr3_note = f" MR3 начнётся только с {fmt_integer(next_mr3)}-го контракта." if next_mr3 is not None else ""
            insight_text = (
                f"Из {fmt_number(calculator_position_contracts, 0)} контрактов "
                f"{fmt_number(calculator_position_margin.level1_contracts, 0)} остаются на MR1, а "
                f"{fmt_number(calculator_position_margin.level2_contracts, 0)} попадают на MR2. "
                "Часть позиции до LK1 не пересчитывается по повышенной ставке." + mr3_note
            )
        else:
            insight_text = (
                f"Позиция разбита ступенчато: MR1 — {fmt_number(calculator_position_margin.level1_contracts, 0)} контр., "
                f"MR2 — {fmt_number(calculator_position_margin.level2_contracts, 0)} контр., "
                f"MR3 — только превышение LK2: {fmt_number(calculator_position_margin.level3_contracts, 0)} контр."
            )
        st.markdown(
            f'<div class="demo-insight"><strong>Ключевая логика ГО</strong><div>{html.escape(insight_text)}</div></div>',
            unsafe_allow_html=True,
        )

    s1, s2, s3 = st.columns(3)
    with s1:
        metric_card(
            "Расчётное ГО", fmt_rub(calculator_position_margin.total_margin_rub),
            "Аналитическая оценка простой позиции", code="TOTAL MARGIN",
            tone="green" if calculator_position_margin.error is None else "neutral",
        )
    with s2:
        metric_card(
            "Достигнутый уровень", calculator_active_level,
            "Максимальный уровень концентрации", code="MR LEVEL",
            tone=calculator_active_tone, compact=True,
        )
    with s3:
        metric_card(
            "Объём в единицах БА", fmt_number(calculator_position_margin.position_ba, 4),
            "Контракты × LOTVOLUME", code="POSITION_BA", tone="blue",
        )

    if calculator_position_margin.error:
        st.warning(f"ГО не рассчитано: {calculator_position_margin.error}.")
    else:
        position_bar(
            calculator_position_margin.level1_contracts,
            calculator_position_margin.level2_contracts,
            calculator_position_margin.level3_contracts,
        )

        st.markdown(
            '<div class="section-head"><div class="section-kicker">Разбивка позиции</div>'
            '<div class="section-title">ГО каждой ступени</div></div>',
            unsafe_allow_html=True,
        )
        g1, g2, g3 = st.columns(3)
        with g1:
            metric_card(
                "MR1 · до LK1", f"{fmt_number(calculator_position_margin.level1_contracts, 0)} контр.",
                f"Ставка {fmt_rate(mr1)} · ГО {fmt_rub(calculator_position_margin.level1_margin_rub)}",
                code="LEVEL 1", tone="green",
            )
        with g2:
            metric_card(
                "MR2 · превышение LK1", f"{fmt_number(calculator_position_margin.level2_contracts, 0)} контр.",
                f"Ставка {fmt_rate(mr2)} · ГО {fmt_rub(calculator_position_margin.level2_margin_rub)}",
                code="LEVEL 2", tone="amber",
            )
        with g3:
            metric_card(
                "MR3 · превышение LK2", f"{fmt_number(calculator_position_margin.level3_contracts, 0)} контр.",
                f"Ставка {fmt_rate(mr3)} · ГО {fmt_rub(calculator_position_margin.level3_margin_rub)}",
                code="LEVEL 3", tone="neutral",
            )

        with st.expander("Показать точную табличную разбивку", expanded=False):
            calculator_breakdown = pd.DataFrame(
                [
                    {
                        "Уровень": "MR1",
                        "Диапазон": "До LK1",
                        "Контрактов": fmt_number(calculator_position_margin.level1_contracts, 4),
                        "Ставка": fmt_rate(mr1),
                        "ГО": fmt_rub(calculator_position_margin.level1_margin_rub),
                    },
                    {
                        "Уровень": "MR2",
                        "Диапазон": "Сверх LK1 до LK2",
                        "Контрактов": fmt_number(calculator_position_margin.level2_contracts, 4),
                        "Ставка": fmt_rate(mr2),
                        "ГО": fmt_rub(calculator_position_margin.level2_margin_rub),
                    },
                    {
                        "Уровень": "MR3",
                        "Диапазон": "Сверх LK2",
                        "Контрактов": fmt_number(calculator_position_margin.level3_contracts, 4),
                        "Ставка": fmt_rate(mr3),
                        "ГО": fmt_rub(calculator_position_margin.level3_margin_rub),
                    },
                ]
            )
            st.dataframe(calculator_breakdown, use_container_width=True, hide_index=True)

        st.caption(
            "Оценка использует официальный INITIALMARGIN первого уровня и масштабирует следующие ступени "
            "через отношения MR2/MR1 и MR3/MR1. Это не полный портфельный расчёт НКЦ."
        )


if active_page == "Методика":
    reference_tab, formulas_tab = st.tabs(["Справочник", "Формулы"])

    with reference_tab:
        st.subheader("Справочник параметров")
        methodology = pd.read_csv(DATA_DIR / "methodology.csv", sep=";", encoding="utf-8-sig", dtype=str)
        methodology_display = methodology[
            ["parameter", "title", "unit", "short_description", "how_to_read"]
        ].rename(
            columns={
                "parameter": "Параметр",
                "title": "Название",
                "unit": "Единица",
                "short_description": "Что означает",
                "how_to_read": "Как интерпретировать",
            }
        )
        methodology_html = methodology_display.to_html(
            index=False,
            escape=True,
            border=0,
            classes="methodology-table",
        )
        st.markdown(
            f'<div class="methodology-table-wrap">{methodology_html}</div>',
            unsafe_allow_html=True,
        )

        st.subheader("Ключевые пояснения")
        with st.expander("Как связаны MR1–MR3 и LK1–LK2", expanded=True):
            st.write(
                "Ставки применяются по уровням концентрации. MR1 относится к объёму в пределах LK1; "
                "MR2 — к части позиции сверх LK1 до LK2; MR3 — к части позиции сверх LK2."
            )
        with st.expander("Почему при пересчёте LK учитывается LOTVOLUME"):
            st.write(
                "LK1 и LK2 относятся к количеству единиц базисного актива. LOTVOLUME показывает, сколько единиц "
                "базисного актива входит в один выбранный фьючерсный контракт. Поэтому сначала LK делится на "
                "LOTVOLUME, и только затем количество контрактов умножается на стоимость одного контракта."
            )
        with st.expander("Почему AutoShiftNumMR — не только основная сессия"):
            st.write(
                "AutoShiftNumMR задаёт общий максимум изменений границ в основную и вечернюю дополнительную сессии. "
                "В вечернюю сессию число сдвигов дополнительно ограничено AutoShiftNumMREvg и остатком общего лимита."
            )
        with st.expander("Дневной и вечерний мониторинг границ"):
            st.write(
                "FutMonTimeDay относится к контролю условия у границы в основной сессии, а FutMonTimeEvg — "
                "к вечерней дополнительной торговой сессии. Вечернее значение загружается из отдельного "
                "официального XLSX НКЦ вместе с AutoShiftNumMREvg."
            )
        with st.expander("Что показывает OffDaysTradingPriceRangeShift"):
            st.write(
                "Это односторонняя доля от модуля цены Pmarket23:50. Например, значение 0,03 означает отклонение "
                "на 3% вверх и на 3% вниз. Для валютных фьючерсов параметр используется в аналитической оценке "
                "утреннего ограничения границ; для остальных БА показывается отдельной карточкой выходных торгов."
            )

    with formulas_tab:
        st.subheader("Формулы, используемые в дашборде")
        st.caption(
            "Денежные значения являются аналитическим пересчётом для выбранной серии. "
            "Они не превращают LK1/LK2 в отдельные официальные денежные риск-параметры."
        )

        st.markdown("#### 1. Стоимость одного контракта")
        st.write("В расчёте лимитов концентрации используется поле `LAST_RUB` выбранного контракта.")
        st.latex(r"LAST\_RUB=\frac{LAST}{MINSTEP}\cdot STEPPRICE")

        st.markdown("#### 2. Биржевые лимиты цены в рублях")
        st.latex(r"LOWLIMIT_{RUB}=\frac{LOWLIMIT}{MINSTEP}\cdot STEPPRICE")
        st.latex(r"HIGHLIMIT_{RUB}=\frac{HIGHLIMIT}{MINSTEP}\cdot STEPPRICE")
        st.caption("Это перевод действующих полей LOWLIMIT и HIGHLIMIT выбранной серии в рублёвый эквивалент.")

        st.markdown("#### 3. Границы валютных фьючерсов в утреннюю сессию")
        st.latex(r"H_{morning}=\min(H_{current},H_{hol}),\qquad L_{morning}=\max(L_{current},L_{hol})")
        st.latex(r"H_{hol}=P_{proxy}+s|P_{proxy}|,\qquad L_{hol}=P_{proxy}-s|P_{proxy}|")
        st.caption(
            "Особый утренний блок применяется только к валютным базисным активам с узкими границами. "
            "s берётся из официального OffDaysTradingPriceRangeShift. Поскольку Pmarket23:50 не публикуется отдельным "
            "полем ISS, дашборд использует явно подписанный прокси — прежде всего PREVSETTLEPRICE. Автоматические "
            "изменения границ в утреннюю дополнительную сессию не выполняются, поэтому число утренних автосдвигов равно нулю."
        )

        st.markdown("#### 4. Лимиты концентрации с учётом размера лота")
        st.latex(r"LK1_{RUB}=\frac{LK1}{LOTVOLUME}\cdot LAST\_RUB")
        st.latex(r"LK2_{RUB}=\frac{LK2}{LOTVOLUME}\cdot LAST\_RUB")

        if (
            not is_missing(lot_volume)
            and not is_missing(last_rub_value)
            and (not is_missing(lk1) or not is_missing(lk2))
        ):
            st.markdown("##### Пример для выбранного контракта")
            example_rows = []
            for label, lk_value, rub_value in (
                ("LK1", lk1, lk1_rub_equivalent),
                ("LK2", lk2, lk2_rub_equivalent),
            ):
                if not is_missing(lk_value):
                    example_rows.append(
                        {
                            "Порог": label,
                            "LK, ед. БА": fmt_integer(lk_value),
                            "LOTVOLUME": fmt_number(lot_volume, 4),
                            "LAST_RUB, ₽": fmt_rub(last_rub_value),
                            "Номинал, ₽": fmt_rub(rub_value),
                        }
                    )
            if example_rows:
                st.dataframe(pd.DataFrame(example_rows), use_container_width=True, hide_index=True)

        st.markdown("#### 5. ГО позиции по уровням концентрации")
        st.latex(r"N_{LK1}=\frac{LK1}{LOTVOLUME},\qquad N_{LK2}=\frac{LK2}{LOTVOLUME}")
        st.latex(r"N_1=\min(|N|,N_{LK1})")
        st.latex(r"N_2=\min\left(\max(|N|-N_{LK1},0),N_{LK2}-N_{LK1}\right)")
        st.latex(r"N_3=\max(|N|-N_{LK2},0)")
        st.latex(r"IM_1=INITIALMARGIN")
        st.latex(r"IM_2=INITIALMARGIN\cdot\frac{MR2}{MR1}")
        st.latex(r"IM_3=INITIALMARGIN\cdot\frac{MR3}{MR1}")
        st.latex(r"GO_{position}=N_1IM_1+N_2IM_2+N_3IM_3")
        st.caption(
            "До LK1 используется официальное INITIALMARGIN выбранной серии. Части позиции выше LK1 "
            "оцениваются пропорционально отношениям MR2/MR1 и MR3/MR1. Повышенная ставка применяется "
            "только к части объёма, попавшей в соответствующий диапазон."
        )
        st.caption(
            "Это аналитическая оценка простой позиции в одной серии, а не полный клиринговый расчёт портфеля НКЦ."
        )
        if not is_missing(initial_margin):
            # Methodology must be self-contained: with lazy navigation the calculator
            # page may never have been rendered in the current session. Reuse its
            # last input when available, otherwise show a deterministic 1-contract example.
            methodology_calc_key = f"dedicated_position_contracts_{selected_contract or assetcode}"
            methodology_position_contracts = st.session_state.get(methodology_calc_key, 1)
            try:
                methodology_position_contracts = max(0, int(methodology_position_contracts))
            except (TypeError, ValueError):
                methodology_position_contracts = 1
            methodology_position_margin = progressive_position_margin(
                methodology_position_contracts,
                lot_volume,
                lk1,
                lk2,
                mr1,
                mr2,
                mr3,
                initial_margin,
            )
            if methodology_position_margin.error is None:
                st.caption(
                    f"Для выбранного контракта INITIALMARGIN = {fmt_rub(initial_margin)}; "
                    f"при {fmt_integer(methodology_position_contracts)} контрактах расчётное ГО = "
                    f"{fmt_rub(methodology_position_margin.total_margin_rub)}."
                )

        st.markdown("#### 6. Параметр для торгов в выходные дни")
        st.write(
            "На дашборде отображается только значение `OffDaysTradingPriceRangeShift = s`. "
            "Абсолютные границы рассчитываются методически от Pmarket23:50, а не от текущего LAST."
        )
        st.latex(
            r"H_{hol}=P_{23:50}+s\cdot|P_{23:50}|,\qquad "
            r"L_{hol}=P_{23:50}-s\cdot|P_{23:50}|"
        )
        st.caption(
            f"Для выбранного assetcode текущее значение параметра: {fmt_rate(weekend_shift)}. "
            "При s = 3% границы строятся как 0,97 × Pmarket23:50 и 1,03 × Pmarket23:50."
        )

        st.markdown("#### 7. Фактический вечерний лимит сдвигов")
        st.latex(
            r"N_{evg,max}=\min\left(AutoShiftNumMREvg,\;"
            r"AutoShiftNumMR-N_{day}\right)"
        )
        st.caption(
            "Из общего лимита вычитается число сдвигов, уже использованных в основной сессии; "
            "результат дополнительно ограничивается AutoShiftNumMREvg."
        )


if PUBLIC_DEPLOYMENT:
    st.markdown(
        """
<div style="margin-top:1.2rem;padding-top:.75rem;border-top:1px solid #E6EAF0;color:#667085;font-size:.72rem;line-height:1.45;">
MOEX Risk Dashboard — аналитический интерфейс на основе официальных данных MOEX / НКЦ.
Расчёт ГО сверх первого уровня и утренние валютные границы являются аналитическими оценками и не заменяют официальный клиринговый расчёт НКЦ.
</div>
""",
        unsafe_allow_html=True,
    )
