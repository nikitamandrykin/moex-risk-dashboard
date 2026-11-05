"""Offline integration smoke: public navigation and customer-mode visibility."""
import os
from time import time
from unittest.mock import patch
import pandas as pd
from streamlit.testing.v1 import AppTest
from services.loaders import SourceStatus

os.environ['MOEX_PUBLIC_DEPLOYMENT'] = '1'
status = SourceStatus('Fixture', 'fallback', 'Offline integration test', '2026-10-21')
contracts = pd.DataFrame([
    dict(assetcode='RTS', secid='RIZ6', shortname='RTS',
         last=99000., lowlimit=80000., highlimit=100000.,
         lasttradedate=pd.Timestamp('2099-12-15'),
         lotsize=1, lotvolume=1, minstep=10, stepprice=10, initialmargin=20000),
    dict(assetcode='AI92', secid='AI92Z6', shortname='Бензин АИ-92',
         last=67600., lowlimit=63650., highlimit=70770.,
         lasttradedate=pd.Timestamp('2099-12-15'),
         lotsize=1, lotvolume=1, minstep=10, stepprice=10, initialmargin=7200),
    dict(assetcode='1MFR', secid='1MFRZ6', shortname='Одномесячная ставка',
         last=9000., lowlimit=8800., highlimit=9200.,
         lasttradedate=pd.Timestamp('2099-12-15'),
         lotsize=1, lotvolume=1, minstep=1, stepprice=1, initialmargin=1100),
])
market = pd.DataFrame([
    dict(assetcode='RTS', title='Индекс РТС',
         mr1=.1, mr2=.15, mr3=.2, lk1=100, lk2=200),
    dict(assetcode='AI92', title='Бензин АИ-92',
         mr1=.1, mr2=.16, mr3=.26, lk1=152, lk2=758),
    dict(assetcode='1MFR', title='Ставка 1MFR',
         mr1=.15, mr2=.2, mr3=.25, lk1=45, lk2=120),
])
empty = pd.DataFrame()
bundle = (market, status, empty, status, empty, status, empty, status,
          contracts, status, empty, status, time())
app = AppTest.from_file('app.py', default_timeout=20)
app.session_state['_source_bundle_v097'] = bundle
app.session_state['_collateral_bundle_v11'] = (empty, status, empty, status)
with patch('requests.get', side_effect=AssertionError('Unexpected network request')):
    app.run()
    assert not app.exception, app.exception
    assert app.session_state['active_page'] == 'Обзор'
    assert all('Состояние источников, обновление и диагностика' != exp.label
               for exp in app.expander)
    for page in ['Обзор', 'Границы', 'Калькулятор ГО', 'Спецрежимы НКЦ', 'Методика']:
        app.segmented_control(key='active_page').set_value(page).run()
        assert not app.exception, (page, app.exception)
    # Reproduce the observed UI discrepancy: a user switches from AI92 to
    # 1MFR. The contract, price cards and heading must follow the same BA.
    app.segmented_control(key='active_page').set_value('Обзор').run()
    app.selectbox(key='_selected_assetcode_widget').set_value('AI92').run()
    assert not app.exception, app.exception
    assert app.session_state['selected_assetcode'] == 'AI92'
    assert app.selectbox(key='_selected_contract_widget_AI92').value == 'AI92Z6'
    selection_cards = [m.value for m in app.markdown if '<div class="selection-compact">' in m.value]
    assert len(selection_cards) == 1 and 'AI92Z6' in selection_cards[0], (
        f"AI92 selection render: {selection_cards!r}; "
        f"asset={app.session_state['selected_assetcode']!r}; "
        f"contract={app.selectbox(key='_selected_contract_widget_AI92').value!r}"
    )

    app.selectbox(key='_selected_assetcode_widget').set_value('1MFR').run()
    assert not app.exception, app.exception
    assert app.session_state['selected_assetcode'] == '1MFR'
    assert app.selectbox(key='_selected_contract_widget_1MFR').value == '1MFRZ6'
    selection_cards = [m.value for m in app.markdown if '<div class="selection-compact">' in m.value]
    assert len(selection_cards) == 1
    assert '1MFRZ6' in selection_cards[0] and 'AI92Z6' not in selection_cards[0]

    app.segmented_control(key='active_page').set_value('Границы').run()
    app.segmented_control(key='active_page').set_value('Обзор').run()
    assert not app.exception, app.exception
    assert app.session_state['selected_assetcode'] == '1MFR'
    assert app.selectbox(key='_selected_contract_widget_1MFR').value == '1MFRZ6'

print('Offline public UI: five published pages and underlying/contract consistency OK')
