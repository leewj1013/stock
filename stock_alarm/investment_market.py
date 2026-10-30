"""Market breadth, recommendation changes and research coverage from saved data."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from .data_store import DB_PATH, query_rows
from .investment_insights import price_bars


def candidates(path=DB_PATH):
    rows = query_rows("""SELECT c.* FROM candidate_snapshots c WHERE run_id=(
        SELECT run_id FROM strategy_runs WHERE status='completed' AND run_type='recommendation'
        ORDER BY started_at DESC LIMIT 1)""", path=path)
    names = query_rows('''SELECT ticker,MAX(pick_date) latest FROM recommendation_outcomes
        GROUP BY ticker ORDER BY latest DESC LIMIT 30''', path=path)
    picks = []
    for name in names:
        picks += query_rows('''SELECT c.* FROM candidate_snapshots c JOIN strategy_runs r USING(run_id)
            WHERE c.ticker=? AND c.selected=1 AND r.status='completed' AND r.run_type='recommendation'
            ORDER BY c.evaluated_at DESC LIMIT 1''', (name['ticker'],), path=path)
    seen = {r['ticker'] for r in rows}
    for pick in picks:
        if pick['ticker'] not in seen:
            rows += query_rows("""SELECT c.* FROM candidate_snapshots c JOIN strategy_runs r USING(run_id)
                WHERE c.ticker=? AND r.status='completed' AND r.run_type='recommendation'
                ORDER BY c.evaluated_at DESC LIMIT 1""", (pick['ticker'],), path=path)
    return rows + picks


def market_breadth(rows, bars_fn=price_bars, now=None):
    from .market_sessions import KST, day_info
    now = now or datetime.now(KST)
    info = day_info(now)
    latest = max((r['evaluated_at'] for r in rows), default='')
    # One completed batch only; never combine today's evaluated stocks and stale ones.
    run_id = next((r['run_id'] for r in rows if r['evaluated_at'] == latest), None)
    batch = {r['ticker']: r for r in rows if r['run_id'] == run_id}
    usable = [r for r in batch.values() if r.get('day_return_pct') is not None]
    rising = sum(r['day_return_pct'] > 0 for r in usable)
    falling = sum(r['day_return_pct'] < 0 for r in usable)
    highs = lows = covered = 0
    cutoff = latest[:10].replace('-', '')
    completed = cutoff < now.strftime('%Y%m%d') or bool(info and info['open'] and now >= info['end'] + timedelta(minutes=30))
    for ticker, item in batch.items():
        bars = sorted((r for r in bars_fn(ticker) if r['date'] <= cutoff), key=lambda r: r['date'])
        # A complete same-date daily bar is needed for a 52-week range comparison.
        if not completed or len(bars) < 253 or bars[-1]['date'] != cutoff:
            continue
        prior = bars[-253:-1]
        highs += float(bars[-1]['high']) >= max(float(r['high']) for r in prior)
        lows += float(bars[-1]['low']) <= min(float(r['low']) for r in prior)
        covered += 1
    result = [['수집된 평가 종목', f'{len(batch)}종목'], ['등락률 확인', f'{len(usable)}/{len(batch)}종목'],
              ['상승 / 하락 / 보합', f'{rising} / {falling} / {len(usable)-rising-falling}'],
              ['상승 종목 비율', f'{rising/len(usable)*100:.1f}%' if usable else '기록 없음'],
              ['52주 신고가 / 신저가', f'{highs} / {lows}' if covered else '동일 날짜 완료 봉 부족'],
              ['52주 비교 가능 표본', f'{covered}/{len(batch)}종목']]
    groups = query_rows("""SELECT name,change_pct,source_time FROM snapshots WHERE category='industries'
        AND market_day=(SELECT MAX(market_day) FROM snapshots WHERE category='industries')
        ORDER BY change_pct DESC""", path='data/market_group_snapshot.db')
    ranking = [[r['name'], f"{r['change_pct']:+.2f}%", r['source_time']]
               for r in (groups[:3] + groups[-3:] if len(groups) > 6 else groups)]
    return {'rows': result, 'sectors': ranking,
            'note': f'{latest or "기록 없음"} 평가 표본이며 전체 KOSPI·KOSDAQ 통계가 아닙니다. 52주는 이전 252개 저장 일봉의 고가·저가와 비교합니다. 기업행동에 따른 가격 기준 변경은 원자료 확인이 필요합니다.'}


def recommendation_changes(rows):
    from .dashboard import display_value
    extra_labels = {'score_or_portfolio_filter': '점수·보유 비중·종목 수 기준으로 미선정',
                    'average_range': '평균 변동폭 기준 초과'}
    latest, picks = {}, {}
    for row in sorted(rows, key=lambda r: r['evaluated_at'], reverse=True):
        latest.setdefault(row['ticker'], row)
        if row.get('selected'):
            picks.setdefault(row['ticker'], row)
    result = []
    for ticker, pick in sorted(picks.items(), key=lambda r: r[1]['evaluated_at'], reverse=True)[:30]:
        current = latest[ticker]
        first, last = pick.get('close'), current.get('close')
        change = (last / first - 1) * 100 if first and last else None
        score, updated = pick.get('final_score'), current.get('final_score')
        delta = updated - score if score is not None and updated is not None else None
        status = ('최근 평가에서 선정' if current.get('selected') else
                  '조건 통과·미선정' if current.get('passed') else '최근 평가 조건 미달')
        reasons = ', '.join(extra_labels.get(part.strip(), display_value(part.strip()))
                            for part in (current.get('rejection_reasons') or '').split(',') if part.strip())
        result.append([pick['evaluated_at'][:10], pick.get('name') or ticker,
                       f'{first:,.0f}원' if first else '기록 없음', f'{last:,.0f}원' if last else '기록 없음',
                       f'{change:+.2f}%' if change is not None else '기록 없음',
                       f'{score:.1f} → {updated:.1f} ({delta:+.1f})' if delta is not None else '점수 기록 없음',
                       status, reasons or '—', current['evaluated_at'].replace('T', ' ')[:16]])
    return {'rows': result, 'note': '종목별 가장 최근 추천 이후의 완료 평가 비교 · 공통 후보 점수 기준. 미선정은 자동 매도 사유가 아니며 과거 추천이 현재 매수 유효함을 뜻하지 않습니다.'}


def market_regime(entry, bars):
    prior = [r for r in bars if r['date'] < entry[:10].replace('-', '')]
    if len(prior) < 60:
        return 'unknown'
    values = [float(r['close']) for r in prior[-60:]]
    return 'bear' if values[-1] < sum(values)/60 and sum(values[-20:])/20 < sum(values)/60 else 'other'


def historical_coverage(directory=Path('reports')):
    """Report historical coverage separately; never add it to forward samples."""
    rows = []
    for slug, label in (('current_data_experiment', '익절·손절 과거 탐색'),
                        ('target_bear_research', '목표 도달·하락장 과거 진단'),
                        ('bear_account_research', '하락장 계좌 원장 재현')):
        try:
            report = json.loads((Path(directory) / slug / 'results.json').read_text(encoding='utf-8'))
            as_of = str(report['generated_at'])[:16]
            if slug == 'bear_account_research':
                sample = f"{report['buys']}매수 · {report['sales']}매도"
                period = f"{report['start']}–{report['cutoff']} · 분석 {as_of}"
            else:
                sample = f"{report['included']}종료 거래 · 과거"
                period = '분석 ' + as_of + ' · 기간은 원 보고서 참조'
            bear = f"{len(report['bear_cases'])}건 · 상세 결과 참조" if 'bear_cases' in report else '장세별 차이 미집계'
            other = f"{report['bear_nonbear']}건 · 별도 비교 없음" if 'bear_nonbear' in report else '장세별 차이 미집계'
            unknown = f"{report['bear_unknown']}건" if 'bear_unknown' in report else '미집계'
            rows.append([label, sample, period, bear, other, unknown, '과거 탐색 · 전향 검증과 별개'])
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return rows


def research_coverage(bars_fn=price_bars):
    from .profit_research import LABELS, DIP_LABELS, PATH, DIP_PATH
    rows = []
    benchmark = sorted(bars_fn('KOSPI'), key=lambda r: r['date'])
    for path, labels in ((PATH, LABELS), (DIP_PATH, DIP_LABELS)):
        cases = query_rows('SELECT * FROM cases ORDER BY trade_id', path=path)
        for key, label in labels.items():
            eligible = []
            for case in cases:
                if not case.get('complete') or case.get('error') or not case.get('outcomes'):
                    continue
                try:
                    outcome = json.loads(case['outcomes'])
                    if path == PATH and key != 'early' and not outcome.get('first_take_profit_at'):
                        continue
                    difference = outcome['arms'][key]['net_return_pct'] - outcome['arms']['baseline']['net_return_pct']
                    eligible.append((case, difference, market_regime(case['bought_at'], benchmark)))
                except (KeyError, ValueError, TypeError):
                    continue
            dates = sorted({c['bought_at'][:10] for c, _, _ in eligible})
            def grouped(kind):
                values = [v for _, v, regime in eligible if regime == kind]
                return f'{sum(values)/len(values):+.3f}%p · {len(values)}건' if values else '0건 · 판단 대기'
            rows.append([label, f'{len(eligible)}/{len(cases)}건',
                         f'{dates[0]}–{dates[-1]} · {len(dates)}진입일' if dates else '완료 표본 없음',
                         grouped('bear'), grouped('other'),
                         f"{sum(regime=='unknown' for _,_,regime in eligible)}건",
                         '전향 관찰 · 독립 검증 전' if eligible else '표본 누적 대기'])
    return {'rows': rows + historical_coverage(), 'note': '신규 관찰은 완료·오류 없는 적격 거래/등록 수, 과거 탐색은 기존 보고서의 분석 표본을 따로 표시하며 합산하지 않습니다. 수치는 기존 청산 대비 거래별 평균 순수익률 차이이며 계좌 수익률이 아닙니다. 하락장은 진입 직전 완료 KOSPI 종가와 20일선이 모두 60일선 아래인 경우, 나머지는 상승·횡보 포함입니다. 같은 종목·기간의 표본은 독립이 아니며 자동 매매 채택 근거로 확정하지 않습니다.'}
