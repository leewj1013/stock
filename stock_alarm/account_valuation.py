"""Shared dashboard valuation from saved, post-entry observations only."""

from datetime import datetime, time, timedelta, timezone
from math import isfinite


def observation_time(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.replace(tzinfo=stamp.tzinfo or timezone(timedelta(hours=9)))
    except (ValueError, TypeError):
        return None


def price_after_entry(observed_at, entry_at):
    observed, entry = observation_time(observed_at), observation_time(entry_at)
    return observed is not None and entry is not None and observed >= entry


def value_saved_account(state, checks, quality, display_marks=(), refresh_status=None, now=None):
    """Use the newest eligible check/quality mark for each current holding.

    Never use a historical account total with current quantities. Missing marks
    leave the account valuation unknown, while cash and realized P&L stay usable.
    """
    now = observation_time(now) if isinstance(now, str) else now or datetime.now(timezone(timedelta(hours=9)))
    now = now.astimezone(timezone(timedelta(hours=9)))
    refresh_status = refresh_status or {}
    marks = {}
    for rows, field, source in ((checks, 'checked_at', '종목 점검'), (quality, 'created_at', '가격 검증')):
        for row in rows:
            if source == '가격 검증' and row.get('status') != 'valid':
                continue
            try:
                price = float(row.get('close') or 0)
            except (ValueError, TypeError):
                continue
            stamp = observation_time(row.get(field))
            if stamp is None or not isfinite(price) or price <= 0:
                continue
            marks.setdefault(row.get('ticker'), []).append((stamp, price, row[field], source, '', ''))
    for row in display_marks:
        stamp = observation_time(row.get('observed_at'))
        price_at = row.get('price_at')
        price = row.get('price')
        if stamp and observation_time(price_at) and isinstance(price, (int, float)) and isfinite(price) and price > 0:
            marks.setdefault(row['ticker'], []).append((stamp, price, row['observed_at'], row['source'], price_at, row['venue']))
    holdings, missing, dates = [], [], []
    for holding in state['holdings']:
        h = dict(holding)
        eligible = [m for m in marks.get(h['ticker'], []) if price_after_entry(m[2], h.get('first_entry_at'))
                    and (not m[4] or price_after_entry(m[4], h.get('first_entry_at')))]
        mark = max(eligible, key=lambda m: (m[0], m[3] == '가격 검증')) if eligible else None
        price = mark[1] if mark else None
        cost, quantity = float(h.get('cost') or 0), int(h.get('quantity') or 0)
        valuation = price * quantity if price is not None else None
        profit = valuation - cost if valuation is not None else None
        h.update(current_price=price, valuation=valuation, profit_loss=profit,
                 return_pct=round(profit / cost * 100, 2) if profit is not None and cost else None,
                 price_observed_at=mark[2] if mark else '', price_source=mark[3] if mark else '',
                 price_data_at=mark[4] if mark else '', price_venue=mark[5] if mark else '')
        stamp = observation_time(h['price_data_at'] or h['price_observed_at'])
        age = max(0, int((now - stamp).total_seconds() / 60)) if stamp else None
        age_text = ('시세 기준 ' if h['price_data_at'] else '관측 후 ') + (
            f'{age // 1440}일 {age % 1440 // 60}시간' if age is not None and age >= 1440 else
            f'{age // 60}시간 {age % 60}분' if age is not None and age >= 60 else f'{age}분') if stamp else '기록 없음'
        prior_day = stamp is not None and stamp.astimezone(now.tzinfo).date() < now.date()
        basis = ('기록 없음' if not stamp else '이전 거래일 가격' if prior_day else
                 '당일 장전가' if h['price_data_at'] and stamp.astimezone(now.tzinfo).time() < time(9)
                 else '당일 점검가')
        attempted = observation_time(refresh_status.get('attempted_at'))
        refresh_failed = (h['ticker'] in refresh_status.get('failed_tickers', []) and attempted is not None
                          and attempted.date() == now.date() and (not mark or mark[0] <= attempted))
        delayed = bool(stamp and now.weekday() < 5 and time(9) <= now.time().replace(tzinfo=None) < time(15, 30) and age > 15)
        label = basis + ' · ' + age_text if stamp else '기록 없음'
        if refresh_failed:
            label += ' · 장전 갱신 실패/마지막 유효가격'
        elif delayed and not prior_day:
            label += ' · 갱신 지연'
        h.update(price_age_minutes=age, price_status_label=label, price_basis_kind=basis,
                 price_warning=bool(not stamp or prior_day or delayed or refresh_failed))
        holdings.append(h)
        if mark:
            dates.append(mark[2])
        else:
            missing.append(h['ticker'])
    cash, deposited = int(state.get('cash') or 0), int(state.get('deposited') or 0)
    cost = sum(float(h.get('cost') or 0) for h in holdings)
    value = sum(h['valuation'] for h in holdings) if not missing else None
    equity = cash + value if value is not None else None
    profit = value - cost if value is not None else None
    return {**state, 'holdings': holdings, 'holdings_value': value, 'total_equity': equity,
            'unrealized_profit_loss': profit, 'total_profit_loss': equity - deposited if equity is not None else None,
            'holdings_return_pct': round(profit / cost * 100, 2) if profit is not None and cost else (0 if not holdings else None),
            'total_return_pct': round((equity / deposited - 1) * 100, 2) if equity is not None and deposited else None,
            'price_unavailable_tickers': missing,
            'price_warning_tickers': [h['ticker'] for h in holdings if h['price_warning']],
            'price_updated_at': max(dates, key=observation_time) if dates else '',
            'price_oldest_at': min(dates, key=observation_time) if dates else '',
            'price_source': '저장된 최신 종목 점검·가격 검증·장전 조회 기록',
            'price_basis_date': max(dates, key=observation_time)[:10] if dates else ''}
