from pathlib import Path

text = Path("app.py").read_text(encoding="utf-8")
monitor_start = text.index('if active_page == "Мониторинг":')
overview_start = text.index('selector_contract_col = None', monitor_start)
monitor = text[monitor_start:overview_start]
assert '"Запрет short"' not in monitor
assert '"Лимит short"' not in monitor
assert '"В обеспечение"' not in monitor
assert '"Лимит обеспечения, %"' not in monitor
assert 'Параметры short/collateral вынесены из Risk Radar' not in monitor
assert 'Параметры базисного актива на других рынках НКЦ' in text
assert 'История приближения к ценовым границам' in monitor
assert 'load_risk_history_cached' in monitor
assert 'MONITOR_CRITICAL_THRESHOLD = 0.75' in monitor
assert '"Ближайшая граница"' in monitor
assert '"Направление"' not in monitor
assert '"CRITICAL / вне границ"' in monitor
assert '"Равноудалены"' in monitor
assert '"Источник цены"' in monitor
assert 'st.altair_chart' in monitor
assert 'Последняя точка графика дополняется текущим live-состоянием' in monitor
print("UI structure: polished Risk Radar plus live-aware boundary history")
