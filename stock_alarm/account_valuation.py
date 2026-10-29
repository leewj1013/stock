"""Shared dashboard valuation from saved, post-entry observations only."""

from datetime import datetime, timedelta, timezone
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


def value_saved_account(state, checks, quality):
    """Use the newest eligible check/quality mark for each current holding.

    Never use a historical account total with current quantities. Missing marks
    leave the account valuation unknown, while cash and realized P&L stay usable.
    """
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
            marks.setdefault(row.get('ticker'), []).append((stamp, price, row[field], source))
    holdings, missing, dates = [], [], []
    for holding in state['holdings']:
        h = dict(holding)
        eligible = [m for m in marks.get(h['ticker'], []) if price_after_entry(m[2], h.get('first_entry_at'))]
        mark = max(eligible, key=lambda m: (m[0], m[3] == '가격 검증')) if eligible else None
        price = mark[1] if mark else None
        cost, quantity = float(h.get('cost') or 0), int(h.get('quantity') or 0)
        valuation = price * quantity if price is not None else None
        profit = valuation - cost if valuation is not None else None
        h.update(current_price=price, valuation=valuation, profit_loss=profit,
                 return_pct=round(profit / cost * 100, 2) if profit is not None and cost else None,
                 price_observed_at=mark[2] if mark else '', price_source=mark[3] if mark else '')
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
            'price_updated_at': max(dates, key=observation_time) if dates else '',
            'price_oldest_at': min(dates, key=observation_time) if dates else '',
            'price_source': '저장된 최신 종목 점검·가격 검증 기록',
            'price_basis_date': max(dates, key=observation_time)[:10] if dates else ''}
