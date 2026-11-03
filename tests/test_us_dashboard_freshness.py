from datetime import datetime
from stock_alarm.dashboard_v2 import us_update_label

def test_us_update_due_time():
    assert us_update_label('2026-10-30',datetime.fromisoformat('2026-11-03T07:59:00+09:00'))=='최신 확정 장부'
    assert '갱신 지연' in us_update_label('2026-10-30',datetime.fromisoformat('2026-11-03T08:00:00+09:00'))
    assert us_update_label('2026-11-02',datetime.fromisoformat('2026-11-03T08:01:00+09:00'))=='최신 확정 장부'
    assert us_update_label('2026-11-25',datetime.fromisoformat('2026-11-27T08:00:00+09:00'))=='최신 확정 장부'
