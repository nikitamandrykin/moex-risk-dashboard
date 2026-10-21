"""Presentation helpers without network or Streamlit side effects."""
import hashlib
import html
import json

from .data_freshness import fetched_at_label, snapshot_expired


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
    @media(max-width:1000px) { .source-grid { grid-template-columns:repeat(3,minmax(0,1fr)); } }
    @media(max-width:600px) { .source-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .block-container { padding-left:1rem; padding-right:1rem; }
      .presentation-hero-tag { display:none; } }
    </style>"""
