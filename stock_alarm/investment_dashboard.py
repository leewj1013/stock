"""Presentation contract for the eight domestic information additions."""
import json
import time
from functools import lru_cache
from html import escape
from pathlib import Path

from .data_store import DB_PATH, query_rows
from .investment_calendar import calendar
from .investment_insights import attribution, concentration, execution_quality, freshness, related_positions, saved_account
from .investment_market import candidates, market_breadth, recommendation_changes, research_coverage
from .trading_profiles import PROFILES
from .investment_timeline import dashboard_data as timeline_data
from .decision_evidence import dashboard_data as evidence_data


def panel(title, headers, data):
    return {'title': title, 'headers': headers, **data}


def account_panels(path, detail=True, profile=None):
    state, buys, sales, deposits, refresh = saved_account(path)
    snapshots = query_rows('SELECT * FROM virtual_valuation_snapshots ORDER BY created_at DESC LIMIT 2000', path=path)
    change = attribution(state, snapshots, deposits, sales)
    prices = freshness(state, refresh)
    try:
        sectors = json.loads(Path('.cache/sector_mapping.json').read_text(encoding='utf-8')).get('mapping', {})
    except (OSError, ValueError):
        sectors = {}
    references, checks = [], []
    for trade in buys[-30:] if detail else []:
        references += query_rows("""SELECT c.* FROM candidate_snapshots c JOIN strategy_runs r USING(run_id)
            WHERE c.ticker=? AND c.evaluated_at<=? AND r.status='completed'
            AND r.run_type='recommendation' ORDER BY c.evaluated_at DESC LIMIT 1""",
            (trade['ticker'], trade['created_at']), path=DB_PATH)
    for sale in sales[-30:] if detail else []:
        checks += query_rows("""SELECT * FROM position_checks WHERE ticker=? AND checked_at<=?
            AND decision='SELL' AND sale_type=? AND COALESCE(stage,'')=?
            ORDER BY checked_at DESC LIMIT 1""",
            (sale['ticker'], sale['created_at'], sale.get('sale_type') or 'full', sale.get('stage') or ''), path=path)
    events = calendar(state['holdings'])
    panels = [panel('계좌 변동 이유', ['항목', '변화 금액'], change),
              panel('가격·수집 상태', ['종목', '관측 시각', '출처', '상태'], prices),
              panel('보유종목 예정 일정', ['종목', '유형', '예정일', '공시', '상태', '출처'], events),
              panel('주요 공시·효력일 확인', ['종목', '유형', '효력일', '공시', '상태', '출처'],
                    {'rows': events['notices'], 'note': events['note']}),
              panel('일정 수집 상태', ['종목', '확인 시각', '상태'], {'rows': events['checks'], 'note': '조회 실패 시 이전 공시를 유지합니다. 실적 발표와 기업설명회는 서로 다른 일정입니다.'}),
              panel('종목·업종 집중도', ['구분', '총자산 대비 비중'], concentration(state, sectors)),
              panel('함께 움직이는 보유종목', ['종목', '비교 종목', '상관계수', '합산 비중', '가격 기준일'], related_positions(state) if detail else {'rows': []}),
              panel('매매 실행 품질', ['체결 시각', '종목', '구분', '대응 신호가', '가상 체결가', '불리한 가격 차이', '기록 비용', '순실현손익'],
                    execution_quality(buys, sales, references, checks))]
    if detail:
        panels.append(panel('판단 시점 기록', ['판단·체결 시각', '유형', '종목', '종목코드', '가격', '점수', '사유', '당시 규칙', '보존 구분', '기록 상태'],
                            evidence_data(path, profile=profile, recommendation_path=PROFILES['aggressive']['db_path'])))
    return {'panels': panels, 'change': change, 'prices': prices, 'events': events}


def _signature():
    files = [Path(p['db_path']) for p in PROFILES.values()]
    files += [Path('data/investment_calendar.db'), Path('data/market_group_snapshot.db'),
              Path('data/investment_timeline.db'),
              Path('data/profit_research.db'), Path('data/profit_research_dips_v1.db'),
              Path('.cache/sector_mapping.json')]
    return tuple((str(p), f.stat().st_mtime_ns, f.stat().st_size)
                 for p in files for f in (p, Path(str(p)+'-wal')) if f.is_file())


def data(view):
    return _cached(view, _signature(), int(time.time() // 30))


@lru_cache(maxsize=8)
def _cached(view, signature, bucket):
    if view == 'research':
        return {'panels': [panel('연구 결과 신뢰도', ['연구', '완료/등록', '관찰 기간', '하락장 차이', '상승·횡보 차이', '장세 미확인', '검증 상태'], research_coverage())]}
    if view == 'signals':
        return {'panels': [panel('추천 이후 변화', ['최근 추천일', '종목', '추천가', '최근 평가가', '가격 변화', '점수 변화', '최근 평가', '제외·미선정 근거', '평가 시각'], recommendation_changes(candidates()))]}
    accounts = []
    for key, name in (('aggressive', '적극투자형'), ('neutral', '위험중립형')):
        path = PROFILES[key]['db_path']
        if Path(path).is_file():
            accounts.append({'name': name, **account_panels(path, detail=view == 'account', profile=key)})
        else:
            accounts.append({'name': name, 'panels': [], 'change': {'available': False},
                             'prices': {'warnings': 0}, 'events': {'rows': [], 'checks': []}})
    if view == 'account':
        return {'accounts': accounts}
    changes, issues, upcoming = [], [], []
    for item in accounts:
        change = item['change']
        changes.append([item['name'], change['rows'][0][1] if change.get('available') else '비교 기록 부족',
                        change['note'].split(' 대비')[0] if change.get('available') else '계좌 기록 없음'])
        if item['prices']['warnings']:
            issues.append([item['name'], f"가격 확인 필요 {item['prices']['warnings']}종목"])
        if change.get('residual'):
            issues.append([item['name'], '계좌 대사 차이 확인 필요'])
        for row in item['events']['checks']:
            if row[2] != '수집 완료':
                issues.append([item['name'] + ' · ' + row[0], '일정 ' + row[2]])
        upcoming += item['events']['rows']
    upcoming = list({tuple(row): row for row in upcoming}.values())
    return {'overview': [panel('오늘 계좌 변화', ['계좌', '변화', '비교 기준'], {'rows': changes, 'note': '전일 이전 마지막 평가 대비 · 입출금 포함. 금액의 원인은 계좌 분석에서 확인하세요.'}),
                         panel('확인 필요한 사항', ['대상', '내용'], {'rows': issues, 'empty': '현재 확인이 필요한 사항이 없습니다.'}),
                         panel('예정 일정', ['종목', '유형', '예정일'],
                               {'rows': [r[:3] for r in sorted(upcoming, key=lambda r: r[2])[:5]],
                                'note': '향후 30일 · 원문과 수집 상태는 계좌 분석에서 확인하세요.',
                                'empty': '확인된 예정 일정이 없습니다. 일정 수집 상태는 계좌에서 확인하세요.'})],
            'panels': [panel('전일 대비 변경 내역', ['변경 유형', '대상', '이전', '현재'], timeline_data()),
                       panel('국내 시장 내부 흐름', ['항목', '결과'], market_breadth(candidates()))]}


def render_panel(item, opened=False):
    def cell(value):
        text = str(value)
        prefix = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='
        if text.startswith(prefix) and len(text.removeprefix(prefix)) == 14 and text.removeprefix(prefix).isdigit():
            return f'<a href="{escape(text, quote=True)}" target="_blank" rel="noopener noreferrer">DART 원문</a>'
        return escape(text)
    heads = ''.join(f'<th scope="col">{escape(h)}</th>' for h in item['headers'])
    body = ''.join('<tr>' + ''.join(f'<td>{cell(v)}</td>' for v in row) + '</tr>' for row in item.get('rows', []))
    table = f'<div class="table-scroll"><table><thead><tr>{heads}</tr></thead><tbody>{body}</tbody></table></div>' if body else '<p class="muted">' + escape(item.get('empty') or '확인된 기록이 없습니다. 수집 상태와 비교 기준을 확인해 주세요.') + '</p>'
    extra = ''
    if item.get('records'):
        extra += '<details><summary>당시 전체 기록 보기</summary>' + ''.join(
            '<details><summary>'+escape(r['title'])+'</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'
            +escape(r['payload'])+'</pre></details>' for r in item['records']) + '</details>'
    if item.get('sectors'):
        extra += render_panel(panel('업종 강세·약세', ['업종', '등락률', '기준 시각'], {'rows': item['sectors']}))
    content = table + '<p class="muted">' + escape(item.get('note') or '') + '</p>' + extra
    return (f'<section><h2>{escape(item["title"])}</h2>{content}</section>' if opened else
            f'<details class="compact-details"><summary>{escape(item["title"])}</summary><div class="details-body">{content}</div></details>')


def sections(view):
    payload = data(view)
    if view == 'account':
        return ''.join('<details class="compact-details"><summary>' + escape(a['name']) + ' · 계좌 분석</summary><div class="details-body">'
                       + ''.join(render_panel(p) for p in a['panels']) + '</div></details>' for a in payload['accounts'])
    return ''.join(render_panel(p, True) for p in payload.get('overview', [])) + ''.join(render_panel(p) for p in payload.get('panels', []))
