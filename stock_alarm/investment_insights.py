"""Read-only domestic account explanations shared by both dashboards."""
from collections import defaultdict
from datetime import datetime
from html import escape
import math
import time
import csv
import json
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from statistics import correlation

from .data_store import query_rows
from .account_valuation import value_saved_account
from .premarket_valuation import read_premarket


def saved_account(path):
    """Reconstruct quantities from ledgers without opening a writable connection."""
    buys = query_rows('SELECT * FROM virtual_trades ORDER BY trade_id', path=path)
    sales = query_rows('SELECT * FROM virtual_sales ORDER BY sale_id', path=path)
    cash = query_rows('SELECT cash FROM virtual_accounts WHERE account_id=1', path=path)
    deposits = query_rows('SELECT * FROM virtual_deposits', path=path)
    holdings = []
    for ticker in sorted({r['ticker'] for r in buys}):
        bought = [r for r in buys if r['ticker'] == ticker]
        sold = [r for r in sales if r['ticker'] == ticker]
        quantity = sum(r['quantity'] for r in bought) - sum(r['quantity'] for r in sold)
        if quantity <= 0:
            continue
        last_full = max((r['sale_id'] for r in sold if r['sale_type'] == 'full'), default=0)
        closed_quantity = sum(r['quantity'] for r in sold if r['sale_id'] <= last_full)
        cumulative = 0
        active = []
        for row in bought:
            cumulative += row['quantity']
            if cumulative > closed_quantity:
                active.append(row)
        cost = sum(r['cost'] for r in bought) - sum(r['cost_basis'] for r in sold)
        holdings.append({'ticker': ticker, 'name': bought[-1].get('name') or ticker,
                         'quantity': quantity, 'cost': cost, 'average_price': cost / quantity,
                         'first_entry_at': active[0]['created_at']})
    state = {'cash': cash[0]['cash'] if cash else 0, 'holdings': holdings,
             'deposited': sum(r['amount'] for r in deposits)}
    checks, quality = [], []
    for h in holdings:
        checks += query_rows('SELECT * FROM position_checks WHERE ticker=? AND checked_at>=? ORDER BY checked_at DESC LIMIT 1',
                             (h['ticker'], h['first_entry_at']), path=path)
        quality += query_rows("SELECT * FROM price_quality WHERE ticker=? AND created_at>=? AND status='valid' ORDER BY created_at DESC LIMIT 1",
                              (h['ticker'], h['first_entry_at']), path=path)
    marks, status = read_premarket(path)
    return value_saved_account(state, checks, quality, marks, status), buys, sales, deposits, status


@lru_cache(maxsize=512)
def _read_bars(filename, modified, size):
    if Path(filename).suffix == '.json':
        rows = json.loads(Path(filename).read_text(encoding='utf-8'))
        return tuple({'date': str(r[0]), 'open': r[1], 'high': r[2], 'low': r[3], 'close': r[4]}
                     for r in rows if isinstance(r, list) and len(r) >= 5)
    with Path(filename).open(encoding='utf-8-sig', newline='') as file:
        return tuple(csv.DictReader(file))


@lru_cache(maxsize=2)
def _cache_index(bucket):
    groups = defaultdict(list)
    folder = Path('.cache/naver')
    if folder.is_dir():
        for file in folder.iterdir():
            pieces = file.stem.rsplit('-', 2)
            if file.suffix == '.json' and len(pieces) == 3 and pieces[1].isdigit() and pieces[2].isdigit():
                groups[pieces[0]].append((pieces[2], pieces[1], file))
    return {ticker: [r[2] for r in sorted(rows, key=lambda r: (-int(r[0]), r[1]))[:5]]
            for ticker, rows in groups.items()}


def price_bars(ticker):
    choices = []
    files = [Path(d) / f'{ticker}.csv' for d in ('data/backtest/ohlcv', 'data/backtest_expanded/pit_universe/ohlcv')]
    files += _cache_index(int(time.time() // 30)).get(ticker, [])
    for file in files:
        if file.is_file():
            stat = file.stat()
            try:
                rows = _read_bars(str(file), stat.st_mtime_ns, stat.st_size)
                if rows:
                    choices.append(rows)
            except (OSError, ValueError, TypeError, IndexError, csv.Error):
                continue
    if not choices:
        return []
    rows = max(choices, key=lambda rows: (rows[-1]['date'], len(rows)))
    clean = {}
    for r in rows:
        try:
            if len(r['date']) != 8 or not r['date'].isdigit() or any(not math.isfinite(float(r[k])) or float(r[k]) <= 0 for k in ('open', 'high', 'low', 'close')):
                continue
            clean[r['date']] = r
        except (KeyError, ValueError, TypeError):
            continue
    return [clean[day] for day in sorted(clean)]


def money(value):
    return f'{value:+,.0f}원' if value is not None else '확인 불가'


def attribution(state, snapshots, deposits, sales, now=None):
    """Reconcile equity to the last saved valuation before today's date."""
    day = (now or datetime.now()).date().isoformat()
    prior = [s for s in snapshots if str(s['created_at'])[:10] < day]
    base = max(prior, key=lambda s: s['created_at']) if prior else None
    if not base or state.get('total_equity') is None:
        return {'available': False, 'rows': [], 'note': '전일 이전 평가 기록 또는 현재 보유가격이 없어 변화를 계산할 수 없습니다.'}
    start = base['created_at']
    end = (now or datetime.now()).isoformat()
    flow = sum(d['amount'] for d in deposits if start < d['created_at'] <= end)
    realized = sum(s['realized_profit_loss'] for s in sales if start < s['created_at'] <= end)
    unrealized = state['unrealized_profit_loss'] - base['profit_loss']
    change = state['total_equity'] - base['equity']
    residual = change - flow - realized - unrealized
    return {'available': True, 'change': change, 'residual': residual,
            'rows': [['총자산 변화', money(change)], ['입출금 기록', money(flow)],
                     ['매도로 확정한 손익', money(realized)], ['평가손익 변화', money(unrealized)],
                     ['기타 조정·대사 차이', money(residual)]],
            'note': f"{start.replace('T', ' ')} 대비 · 저장가격 기준. 평가손익 변화에는 신규 매수 비용 영향이 포함됩니다. 매수·매도 대금 자체는 자산 간 이동입니다."}


def concentration(state, sectors):
    total = state.get('total_equity')
    if total is None or total <= 0:
        return {'rows': [], 'note': '전체 보유가격이 확인되어야 비중을 계산할 수 있습니다.'}
    groups = defaultdict(float)
    stocks = []
    for h in state['holdings']:
        value = h.get('valuation')
        if value is None:
            continue
        groups[sectors.get(h['ticker']) or '미분류'] += value
        stocks.append([h.get('name') or h['ticker'], value / total * 100])
    stocks.sort(key=lambda r: -r[1])
    rows = [['현금 비중', f"{state['cash'] / total * 100:.1f}%"],
            ['상위 3종목 합계', f'{sum(r[1] for r in stocks[:3]):.1f}%']]
    rows += [[f'종목 · {name}', f'{weight:.1f}%'] for name, weight in stocks]
    rows += [[f'업종 · {name}', f'{value / total * 100:.1f}%']
             for name, value in sorted(groups.items(), key=lambda r: -r[1])]
    return {'rows': rows, 'note': '총자산 대비 비중 · 미분류는 동일 업종이라는 뜻이 아닙니다.'}


def freshness(state, refresh_status):
    rows = [[h.get('name') or h['ticker'], h.get('price_observed_at') or '기록 없음',
             h.get('price_source') or '기록 없음', h.get('price_status_label') or '기록 없음']
            for h in state['holdings']]
    return {'rows': rows, 'warnings': len(state.get('price_warning_tickers', [])),
            'note': '마지막 가격 관측 ' + (state.get('price_updated_at') or '기록 없음')
                    + ' · 장전 수집 ' + (refresh_status.get('state') or '기록 없음')
                    + ' · 시세 기준시각이 없는 기록은 조회시각과 구분해서 읽어 주세요.'}


def related_positions(state, bars_fn=price_bars):
    """Pairwise correlations require the same 20 consecutive return intervals."""
    holdings = state['holdings']
    series = {}
    for h in holdings:
        cutoff = str(h.get('price_data_at') or h.get('price_observed_at') or '')[:10].replace('-', '')
        bars = sorted((r for r in bars_fn(h['ticker']) if r['date'] <= cutoff), key=lambda r: r['date'])[-61:]
        returns = {}
        for prev, current in zip(bars, bars[1:]):
            before, after = float(prev['close']), float(current['close'])
            if before > 0 and after > 0 and math.isfinite(before + after):
                returns[(prev['date'], current['date'])] = after / before - 1
        series[h['ticker']] = returns
    rows, covered = [], 0
    for first, second in combinations(holdings, 2):
        a, b = series[first['ticker']], series[second['ticker']]
        common = sorted(set(a) & set(b))[-20:]
        if len(common) != 20 or any(x[1] != y[0] for x, y in zip(common, common[1:])):
            continue
        try:
            value = correlation([a[d] for d in common], [b[d] for d in common])
        except (ValueError, ArithmeticError):
            continue
        covered += 1
        if value >= .8:
            total = state.get('total_equity')
            weight = ((first['valuation'] + second['valuation']) / total * 100
                      if total and first.get('valuation') is not None and second.get('valuation') is not None else None)
            rows.append([first.get('name') or first['ticker'], second.get('name') or second['ticker'],
                         f'{value:.2f}', f'{weight:.1f}%' if weight is not None else '확인 불가', common[-1][1]])
    return {'rows': rows, 'note': f'공통 연속 20거래일 수익률 · 상관계수 0.80 이상만 표시 · 계산 가능 {covered}/{len(holdings)*(len(holdings)-1)//2}쌍. 같은 날짜 구간만 비교하며 업종과 별개입니다.'}


def execution_quality(buys, sales, candidates, checks):
    rows = []
    for side, trades in (('매수', buys), ('매도', sales)):
        for trade in trades[-30:]:
            stamp = trade['created_at']
            field = 'evaluated_at' if side == '매수' else 'checked_at'
            references = [r for r in (candidates if side == '매수' else checks)
                          if r['ticker'] == trade['ticker'] and r[field] <= stamp
                          and r[field][:10] == stamp[:10]
                          and (side == '매수' or (r.get('decision') == 'SELL'
                               and (r.get('sale_type') or 'full') == (trade.get('sale_type') or 'full')
                               and (r.get('stage') or '') == (trade.get('stage') or '')))]
            ref = max(references, key=lambda r: r[field]) if references else None
            price = float(ref['close']) if ref and ref.get('close') else None
            # Manual entries have no recommendation reference.
            if side == '매수' and trade.get('source') != 'recommendation':
                price = None
            adverse = (trade['price'] / price - 1) * (1 if side == '매수' else -1) * 100 if price and price > 0 else None
            gross = trade['price'] * trade['quantity']
            fee = trade['cost'] - gross if side == '매수' else gross - trade['proceeds']
            rows.append([stamp.replace('T', ' ')[:16], trade.get('name') or trade['ticker'], side,
                         f'{price:,.0f}원' if price else '대응 신호 없음', f"{trade['price']:,.0f}원",
                         f'{0 if abs(adverse) < .0005 else adverse:+.3f}%' if adverse is not None else '비교 불가',
                         f'{fee:,.0f}원' if fee >= 0 else '비용 기록 확인 필요',
                         money(trade.get('realized_profit_loss')) if side == '매도' else '미실현'])
    rows.sort(reverse=True)
    return {'rows': rows[:40], 'note': '가상 체결 기준 · 같은 날 체결 이전에 저장된 대응 신호와 비교. +차이는 불리한 체결입니다. 비용은 장부 대금 차이이며 미기록 비용을 추정하지 않습니다. 실현손익은 매수원가·매도비용 반영 후 금액입니다.'}
