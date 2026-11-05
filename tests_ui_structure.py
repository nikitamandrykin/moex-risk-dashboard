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
assert 'monitor_search = st.text_input(' in monitor
assert 'monitor_detailed = st.toggle(' in monitor
assert '"Положение, %"' in monitor
assert 'if not monitor_detailed:' in monitor
assert 'monitor_page_size = 16' in monitor
assert 'height="content"' in monitor
assert 'visible_display.iloc[selected_idx]' in monitor
assert 'st.segmented_control(' in text
assert 'NAV_PAGES = ["Обзор", "Границы", "Калькулятор ГО", "Спецрежимы НКЦ", "Методика"]' in text
assert 'if not PUBLIC_DEPLOYMENT:\n    with st.expander("Состояние источников, обновление и диагностика"' in text
assert 'st.session_state["active_page"] = "Обзор"' in text
assert 'parameter_reference_html(methodology.to_dict("records"))' in text
assert 'methodology_html = methodology_display.to_html(' not in text
assert 'Последняя точка графика дополняется текущим live-состоянием' in monitor
print("UI structure: polished Risk Radar plus live-aware boundary history")
