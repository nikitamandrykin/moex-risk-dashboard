"""Offline integration smoke: public navigation and empty search."""
import os
from time import time
from unittest.mock import patch
import pandas as pd
from streamlit.testing.v1 import AppTest
from services.loaders import SourceStatus

os.environ['MOEX_PUBLIC_DEPLOYMENT'] = '1'
status = SourceStatus('Fixture', 'fallback', 'Offline integration test', '2026-10-21')
contracts = pd.DataFrame([dict(assetcode='RTS', secid='RIZ6', shortname='RTS',
    last=99000., lowlimit=80000., highlimit=100000., lasttradedate=pd.Timestamp('2099-12-15'),
    lotsize=1, lotvolume=1, minstep=10, stepprice=10, initialmargin=20000)])
market = pd.DataFrame([dict(assetcode='RTS', title='Индекс РТС', mr1=.1, mr2=.15, mr3=.2, lk1=100, lk2=200)])
empty = pd.DataFrame()
bundle = (market, status, empty, status, empty, status, empty, status,
          contracts, status, empty, status, time())
app = AppTest.from_file('app.py', default_timeout=20)
app.session_state['_source_bundle_v097'] = bundle
app.session_state['_collateral_bundle_v11'] = (empty, status, empty, status)
with patch('requests.get', side_effect=AssertionError('Unexpected network request')):
    app.run()
    assert not app.exception, app.exception
    app.text_input(key='monitor_search').set_value('no such instrument').run()
    assert not app.exception, app.exception
    assert any('ничего не найдено' in item.value for item in app.info)
    for page in ['Обзор', 'Границы', 'Калькулятор ГО', 'Спецрежимы НКЦ', 'Методика', 'Мониторинг']:
        app.radio(key='active_page').set_value(page).run()
        assert not app.exception, (page, app.exception)
print('Offline public UI: search, empty results and all six pages OK')
