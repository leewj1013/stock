from datetime import datetime
from zoneinfo import ZoneInfo
from stock_alarm import us_history_collect as history


def test_year_boundary_and_incomplete_session_filter(monkeypatch):
    requests=[]
    monkeypatch.setattr(history,'get',lambda path:requests.append(path) or [{'localDate':'20270104'},{'localDate':'20270105'}])
    now=datetime(2027,1,4,18,tzinfo=ZoneInfo('America/New_York'))
    assert history.history('AAPL.O',now)==[{'localDate':'20270104'}]
    assert 'endDateTime=202701050000' in requests[0]


def test_scheduled_post_close_cutoff_handles_dst_and_standard_time():
    for month,day in [(10,30),(11,3)]:
        now=datetime(2026,month,day,6,45,tzinfo=ZoneInfo('Asia/Seoul'))
        assert history.completed_day(now).day==day-1
    now=datetime(2027,1,4,15,tzinfo=ZoneInfo('America/New_York'))
    assert history.completed_day(now).isoformat()=='2027-01-03'
