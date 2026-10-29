from datetime import date
from unittest.mock import Mock

from stock_alarm import krx_research_collect as collector


def test_only_completed_months_and_observed_sessions_are_used():
    days=['20260929','20260930','20261028','20260130','20260129']
    assert collector.month_ends(days,date(2026,10,29))==['20260130','20260930']


def test_requests_skip_existing_and_legacy_balance_without_confusing_turnover():
    existing={('industry','20260930','KOSPI')}
    legacy={('20260831','KOSPI'),('20260831','KOSDAQ')}
    tasks=collector.pending_requests(['20260831','20260930'],existing,legacy,False)
    assert tasks==[('industry','20260930','KOSDAQ'),('short_balance','20260930','KOSPI'),('short_balance','20260930','KOSDAQ')]


def test_snapshot_is_resumable_and_does_not_rewrite_inputs(tmp_path,monkeypatch):
    client=Mock();frame=Mock();frame.empty=False;frame.__len__=Mock(return_value=2)
    frame.reset_index.return_value.to_json.return_value='[{"ticker":"A"},{"ticker":"B"}]'
    client.get_market_sector_classifications.return_value=frame
    monkeypatch.setattr(collector.time,'sleep',lambda _:None)
    path=str(tmp_path/'research.db')
    result=collector.run(path,True,client,days=['20260930'],legacy={('20260930','KOSPI'),('20260930','KOSDAQ')})
    assert result=={'snapshots':2,'rows':4,'failures':0}
    assert collector.run(path,True,client,days=['20260930'],legacy={('20260930','KOSPI'),('20260930','KOSDAQ')})['snapshots']==0
