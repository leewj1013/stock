/* Read-only v2 preview. The API returns saved records; unavailable fields stay empty. */
const escapeData = value => typeof value === 'string' ? value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])) : Array.isArray(value) ? value.map(escapeData) : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([k,v]) => [k,escapeData(v)])) : value;

marketChart = function () {
  const d = sample[state.market], all = d.chart || [];
  const count = {'1주':5,'1개월':22,'3개월':66,'1년':252}[state.marketPeriod];
  const rows = all.slice(-count);
  if (rows.length < 2) return '<div class="empty">이 기간의 지수 기록이 부족합니다.</div>';
  const values = rows.map(r => Number(r[1])), low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
  const coords = values.map((v,i) => `${(45+i*370/(values.length-1)).toFixed(1)},${(20+(high-v)/span*93).toFixed(1)}`).join(' ');
  const labels = [0,1,2,3].map(i => `<span class="axis-y axis-${i}">${(high-(span*i/3)).toFixed(0)}</span>`).join('');
  const first = rows[0][0].replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3'), last = rows.at(-1)[0].replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3');
  return `<div class="chart-frame market-frame"><svg class="market-chart" viewBox="0 0 430 151" preserveAspectRatio="none" role="img" aria-label="${d.bench} ${state.marketPeriod} 저장된 일별 지수 흐름"><path d="M45 20H420M45 50H420M45 80H420M45 110H420" stroke="#e5eaed" stroke-dasharray="2 3"/><polygon points="45,116 ${coords} 415,116" fill="#ee5360" opacity=".09"/><polyline points="${coords}" fill="none" stroke="#ee5360" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>${labels}<span class="axis-x axis-start">${first.slice(5)}</span><span class="axis-x axis-end">${last.slice(5)}</span></div>`;
};

renderers.today = function () {
  const d = sample[state.market], buys = d.signals.filter(x => x[1] === '매수 추천').length, sells = d.signals.filter(x => x[1] === '매도 검토').length;
  const notice = state.market === 'us' ? `최근 전략 결정 ${d.signalHistoryCount}건 · 상세 기록은 추천·신호에서 확인` : `저장된 매수 추천 ${buys}건 · 매도 검토 ${sells}건`;
  const virtual = d.accounts.filter(a=>a.source!=='real');
  const price = state.market === 'kr' ? `<div class="account-info"><b>가상계좌 가격 점검</b> · ${virtual.map(a=>`${a.name} ${a.priceAsOf || '기록 없음'}`).join(' / ')} · 확인 필요 ${virtual.reduce((n,a)=>n+(a.priceIssues?.length||0),0)}종목 <a href="#account" data-link="account">계좌에서 확인 →</a></div>` : '';
  const heldSells = state.market === 'kr' && d.heldSells.length ? `<section class="panel pad held-sells-panel"><div class="panel-head"><h2>보유종목 매도 신호</h2><small>${d.heldSells.length}건</small></div>${table(['종목','적극투자형','위험중립형'],d.heldSells.map(x=>`<tr>${x.map((v,i)=>`<td class="${i===0?'name':''}">${v}</td>`).join('')}</tr>`),'held-sells',15)}</section>` : '';
  const recent = state.market === 'us' ? d.recentDecisions.map(x=>[x.name,x.signal,x.reason,x.date.replace('T',' ').slice(0,16),x.status]) : d.signals.slice(0,7);
  return `<div class="today-page">${heading('오늘의 투자 현황','저장된 시장·계좌·신호 기록을 한눈에 확인하세요.')}<div class="notice ${d.signals.length?'':'neutral'}"><span>${notice}</span><a href="#signals" data-link="signals">신호 전체 보기 →</a></div>${price}${heldSells}<div class="grid two"><section class="panel pad"><div class="panel-head"><h2>${state.market==='us'?'최근 전략 결정':'최근 신호'}</h2><small>총 ${state.market==='us'?d.signalHistoryCount:d.signals.length}건</small></div>${recent.length ? table(['종목','신호','이유',state.market==='us'?'발생일':'시각','상태'],signalRows(recent)) : '<div class="empty">기록 없음</div>'}</section>${accountSummary()}</div><div class="grid equal lower">${marketPanel()}<section class="panel pad"><div class="panel-head"><h2>${d.bench} 지수 차트</h2><div class="period-switch" role="group" aria-label="지수 차트 기간">${['1주','1개월','3개월','1년'].map(x=>`<button type="button" data-market-period="${x}" aria-pressed="${state.marketPeriod===x}">${x}</button>`).join('')}</div></div>${marketChart()}</section></div></div>`;
};

renderers.signals = function () {
  const d = sample[state.market], rows = d.signalHistory || [], total = d.signalHistoryTotal || 0;
  const page = state.paging.signals || 0, pages = Math.ceil(total / 20);
  const cell = value => value && value !== '기록 없음' ? value : '—';
  const profit = x => `<td class="${x.returnPct.startsWith('+')?'positive':x.returnPct.startsWith('-')?'negative':''}">${x.returnPct}</td>`;
  const name = x => `<td class="name">${x.name}${x.ticker?` <span class="muted">${x.ticker}</span>`:''}</td>`;
  const domestic = state.market === 'kr', sellOnly = state.filter === '매도 신호';
  const headers = domestic ? (sellOnly ? ['매도 신호일','종목','신호','매도 알림가','수익률','상태','사유'] : ['추천일','종목','추천가','현재가','매도 신호일','매도 알림가','수익률','상태','이유']) : [sellOnly?'전략 매도일':'발생일','종목','신호','결정가','상태','이유','전략'];
  const body = rows.map(x => {
    const sell = x.signal.includes('매도'), detail = x.detail || {};
    const date = x.date.replace('T',' ').slice(0,16);
    if (domestic && sellOnly) return `<tr><td>${cell(detail.sellDate || date)}</td>${name(x)}<td>${badge(x.signal)}</td><td>${cell(detail.sellPrice)}</td>${profit(x)}<td>${x.status}</td><td class="reason">${x.reason}</td></tr>`;
    if (domestic) return `<tr><td>${sell?'—':date}</td>${name(x)}<td>${cell(detail.entryPrice)}</td><td>${cell(detail.currentPrice)}</td><td>${cell(detail.sellDate || (sell ? date : ''))}</td><td>${cell(detail.sellPrice)}</td>${profit(x)}<td>${x.status}</td><td class="reason">${x.reason}</td></tr>`;
    return `<tr><td>${date}</td>${name(x)}<td>${badge(x.signal)}</td><td>${cell(detail.decisionPrice)}</td><td>${x.status}</td><td class="reason">${x.reason}</td><td>${x.source}</td></tr>`;
  });
  const pager = pages > 1 ? `<div class="pager"><span>${page*20+1}–${Math.min((page+1)*20,total)} / ${total}건</span>${[['«',0],['‹',Math.max(0,page-1)]].map(([label,n])=>`<button type="button" data-table-page="signals:${n}" ${page===0?'disabled':''} aria-label="${label==='«'?'첫':'이전'} 페이지">${label}</button>`).join('')}${Array.from({length:Math.min(5,pages)},(_,i)=>Math.floor(page/5)*5+i).filter(n=>n<pages).map(n=>`<button type="button" data-table-page="signals:${n}" class="${n===page?'active':''}" ${n===page?'aria-current="page"':''}>${n+1}</button>`).join('')}${[['›',Math.min(pages-1,page+1)],['»',pages-1]].map(([label,n])=>`<button type="button" data-table-page="signals:${n}" ${page===pages-1?'disabled':''} aria-label="${label==='»'?'마지막':'다음'} 페이지">${label}</button>`).join('')}</div>` : '';
  return heading('추천·신호','지금까지 저장된 국내 추천·매도 알림과 미국 전략 결정 기록입니다.') + `<section class="panel pad signal-grid"><div class="toolbar"><div class="chips">${(domestic?['전체','매도 신호','추적 중']:['전체','매수 추천','매도 신호','추적 중']).map(x=>`<button type="button" data-filter="${x}" aria-pressed="${state.filter===x}">${x}</button>`).join('')}</div><input class="search" type="search" aria-label="종목 검색" placeholder="종목·이유 검색"></div><p class="hint">현재 조건 ${total}건 · 최근 기록부터 표시${domestic?' · 전체는 추천일·종목당 한 행이며 매도 알림을 함께 표시합니다. 개별 알림은 매도 신호 탭에서 확인하세요. 현재가는 마지막 저장 추적가입니다. 매도 신호일·알림가는 실제 체결 기록이 아닙니다.':''}</p>${body.length ? table(headers,body) + pager : '<div class="empty">해당 조건의 저장 기록이 없습니다.</div>'}</section>`;
};

function comparisonChart() {
  const data = sample[state.market].compareChart || {days:[],series:[]};
  if (data.days.length < 2 || !data.series.length) return '<div class="empty">비교할 공통 거래일 기록이 부족합니다.</div>';
  const all = data.series.flatMap(s=>s.values).concat(0), low = Math.min(...all), high = Math.max(...all), span = high-low || 1;
  const y = v => 20+(high-v)/span*170;
  const grid = [0,1,2,3].map(i=>{const value=high-span*i/3, pos=y(value).toFixed(1);return `<path d="M52 ${pos}H420" stroke="#e5eaed" stroke-dasharray="2 3"/>`}).join('');
  const labels = [0,1,2,3].map(i=>{const value=high-span*i/3;return `<span class="axis-y axis-${i}">${value.toFixed(1)}%</span>`}).join('');
  const lines = data.series.map(s=>`<polyline points="${s.values.map((v,i)=>`${(52+i*360/(data.days.length-1)).toFixed(1)},${y(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`).join('');
  const legend = data.series.map((s,i)=>`<span><i class="series-${i%3}"></i>${s.label} <b class="${changeClass(s.values.at(-1))}">${s.values.at(-1).toFixed(2)}%</b></span>`).join('');
  return `<div class="chart-key">${legend}</div><div class="chart-frame compare-frame"><svg class="compare-chart" viewBox="0 0 430 225" preserveAspectRatio="none" role="img" aria-label="${data.days[0]}부터 ${data.days.at(-1)}까지 공통 거래일 누적 수익률 비교">${grid}${lines}</svg>${labels}<span class="axis-x axis-start">${data.days[0].slice(5)}</span><span class="axis-x axis-end">${data.days.at(-1).slice(5)}</span></div>`;
}

renderers.compare = function () {
  const d = sample[state.market];
  const pct = value => value === '기록 없음' ? value : `${value}%`;
  const experiments = `<h2 class="section-title">관찰용 실험 계좌 · 위험지표</h2><section class="panel pad">${d.experiments.length?table(['실험 계좌','시작일','현재 자산','총수익률','최대낙폭','샤프지수','상태'],d.experiments.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[2]}</td><td>${x[3]}</td><td class="${changeClass(x[4])}">${pct(x[4])}</td><td>${pct(x[5])}</td><td>${x[6]}</td><td>${x[9]}</td></tr>`),'experiments',15):'<div class="empty">실험 계좌 기록이 없습니다.</div>'}</section><p class="hint">관찰 전용 계좌입니다. 시작일이 다르면 총수익률을 직접 비교하지 마세요. 미국 최대낙폭은 저장된 첫 평가일 이후 기준이며 샤프지수는 수익률 기록이 20개 이상일 때 표시합니다.</p>`;
  return heading('성과 비교','공통 거래일을 시작점 0%로 맞춰 계좌와 지수를 비교합니다.') + `<section class="panel pad"><div class="panel-head"><h2>같은 기간의 누적 수익률</h2><small>${d.compareChart.days.length}거래일</small></div>${comparisonChart()}</section><h2 class="section-title">계좌별 현재 기록</h2><section class="panel pad">${table(['계좌','총자산',state.market==='kr'?'입금액 대비 수익률':'누적 수익률',state.market==='kr'?'최근 저장 평가 변화':'최근 평가 변화'],d.accounts.filter(a=>a.source!=='real').map(a=>`<tr><td class="name">${a.name}</td><td>${a.asset}</td><td class="${changeClass(a.returnPct)}">${a.returnPct}</td><td class="${changeClass(a.daily)}">${a.daily}</td></tr>`))}</section>${experiments}<div class="footer-note">차트는 입금 영향을 제거한 국내 가상계좌 수익률과 미국 전략·SPY 장부의 공통 날짜만 사용합니다.</div>`;
};

renderers.account = function () {
  const d = sample[state.market], a = d.accounts[state.account] || d.accounts[0];
  const choices = `<div class="account-choice">${d.accounts.map((x,i)=>`<button type="button" data-account="${i}" aria-pressed="${state.account===i}">${x.name}</button>`).join('')}</div>`;
  if (a.source !== 'real') {
    const domestic = state.market === 'kr';
    const headers = domestic ? ['종목','수량','보유 비중','보유일수','평균매수가','저장 평가가','가격 관측 시각','평가손익','수익률','매도 감시상태','손절 기준','20일선'] : ['종목','보유 비중','평가손익','수익률','상태'];
    return heading('계좌','계좌별 자산과 보유종목의 저장 기록을 확인합니다.') + choices + `<div class="account-info"><b>${a.name}</b> · ${a.kind}</div><div class="wide-metrics">${metric('총자산',a.asset)}${metric(domestic?'최근 저장 평가 변화':'최근 평가 변화',a.daily)}${metric('현금',a.cash)}${metric(domestic?'입금액 대비 수익률':'누적 수익률',a.returnPct)}</div><h2 class="section-title">현재 보유종목 · ${a.hold}</h2><section class="panel pad">${a.rows.length ? table(headers,a.rows.map(row=>`<tr>${row.map((cell,i)=>`<td class="${i===0?'name':(domestic?[7,8]:[2,3]).includes(i)?changeClass(cell):''}">${cell}</td>`).join('')}</tr>`),'holdings',15) : '<div class="empty">보유종목 기록이 없습니다.</div>'}</section>${domestic?`<p class="hint">가격 관측 ${a.priceOldestAt || a.priceAsOf} ~ ${a.priceAsOf} · 가격 확인 필요 ${a.priceIssues.length}종목${a.priceIssues.length?` (${a.priceIssues.join(', ')})`:''} · 총자산과 종목별 손익은 같은 저장가격으로 계산합니다. 유효 가격이 없는 종목이 있으면 계좌 평가액·수익률을 표시하지 않습니다. 최근 저장 평가 변화는 과거 계좌 평가 기록 간 차이입니다.</p><h2 class="section-title">매도 내역 · 누적 실현손익 ${a.realized}</h2><section class="panel pad">${a.sales.length?table(['매도일','종목','구분','수량','매도가','실현손익','수익률','사유'],a.sales.map(row=>`<tr>${row.map((cell,i)=>`<td class="${i===1?'name':i===7?'reason':[5,6].includes(i)?changeClass(cell):''}">${cell}</td>`).join('')}</tr>`),'sales',15):'<div class="empty">저장된 매도 내역이 없습니다.</div>'}</section>`:''}`;
  }
  const intro = heading('계좌','토스증권 국내주식 실계좌를 읽기 전용으로 조회합니다.') + choices;
  if (a.connected === null) return intro + '<section class="panel pad"><div class="empty">실계좌를 조회하고 있습니다.</div></section>';
  if (!a.connected) return intro + `<section class="panel pad"><div class="panel-head"><h2>실계좌 연결 안 됨</h2><button type="button" data-refresh-real="1">다시 조회</button></div><div class="empty">${a.reason}</div></section>`;
  return intro + `<div class="account-info"><b>실계좌 연결됨</b> · 국내주식만 표시 · 조회 시각 ${a.asOf} <button type="button" data-refresh-real="1">새로고침</button></div><div class="wide-metrics">${metric('총자산',a.asset)}${metric('주문 가능 현금',a.cash)}${metric('평가손익',a.unrealized)}${metric('보유주식 평가수익률',a.returnPct)}</div><h2 class="section-title">현재 보유종목 · ${a.hold}</h2><section class="panel pad">${a.rows.length ? table(['종목','수량','평균매수가','현재가','평가액','평가손익','수익률','상태'],a.rows.map(row=>`<tr>${row.map((cell,i)=>`<td class="${i===0?'name':[5,6].includes(i)?changeClass(cell):''}">${cell}</td>`).join('')}</tr>`),'real-holdings',15) : '<div class="empty">보유 중인 국내주식이 없습니다.</div>'}</section><p class="hint">실계좌 수익률은 현재 보유 중인 국내주식의 평가손익 ÷ 매입원가입니다. 현금과 과거 매도 손익은 포함하지 않습니다.</p>`;
};

async function loadRealAccount() {
  const account = sample.kr.accounts.find(a=>a.source==='real');
  if (!account) return;
  account.connected = null;
  if (state.market==='kr' && state.page==='account' && sample.kr.accounts[state.account]===account) render();
  try {
    const response = await fetch('/api/v2/real-account', {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    Object.assign(account, escapeData(await response.json()));
  } catch (_) {
    Object.assign(account, {connected:false, reason:'실계좌 서버에 연결할 수 없습니다.'});
  }
  if (state.market==='kr' && state.page==='account' && sample.kr.accounts[state.account]===account) render();
}

document.addEventListener('click', event => {
  const button = event.target.closest('[data-account],[data-refresh-real]');
  if (button && state.market==='kr' && state.page==='account' && sample.kr && sample.kr.accounts[state.account]?.source==='real') loadRealAccount();
});

const renderResearchDetails = function () {
  const d = sample[state.market], rows = d.research || [], filtered = rows.filter(x => state.researchFilter === '전체' || x[4] === state.researchFilter);
  const profit = d.profitResearch;
  const profitResearch = state.market === 'kr' && profit ? `<h2 class="section-title">하락장 대응 제안 · 매수·매도 추가 실험</h2><section class="panel pad"><p class="hint">매수 축소·보류는 과거 거래·계좌 원장 비교를 표시하며 익절 앞당김은 거래별 비교입니다. 직전 완료 거래일 KOSPI 종가와 20일선이 모두 60일선 아래인 날을 대상으로 하는 안입니다. 자료 부족일은 제외하고 회복 뒤 20거래일까지 반등 기회·현금 비중·계좌 최대낙폭을 비교합니다.</p>${table(['연구','상태','비교 규칙'],profit.plans.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[1]}</td><td class="reason">${x[2]}</td></tr>`),'profit-plans',15)}<p class="hint">관찰 시작 ${profit.start} · 신규 가상매수 ${profit.total}건 · 종료 ${profit.complete}건 · 오류 ${profit.errors}건</p>${table(['실험','종료 표본','평균 순수익률 차이','기존보다 유리한 건수'],profit.results.map(x=>`<tr>${x.map(v=>`<td>${v}</td>`).join('')}</tr>`),'profit-results',15)}<p class="hint">국내 적극투자형 신규 가상매수 관찰입니다. 기존 가상 체결과 비교하며 추가 신호는 다음 저장 가격에 체결한다고 가정합니다. 기존 전량매도 시점까지 두 매도 물량 손익을 합산하고 매수·매도 비용 각 0.15%를 반영합니다. 완료 건만 평균에 포함하며 원금 보호·추적 청산은 1차 익절 체결 건만 집계합니다. 매수가 청산선은 비용·갭 때문에 본전을 보장하지 않습니다. 계좌 수익률·최대낙폭 비교는 아니며 점검 사이 가격은 확인할 수 없습니다. 가격 부담 보류안은 기존 매수 연구를 재사용합니다. 자동 주문에는 적용하지 않습니다.</p></section>` : '';
  const historical = profit?.historical;
  const historyTable = (heads, rows, id) => table(heads, rows.map(row=>`<tr>${row.map(value=>`<td>${value}</td>`).join('')}</tr>`),id,15);
  const historicalResearch = state.market === 'kr' && profit ? `<h2 class="section-title">현재 자료 실험 결과 · 과거 탐색</h2><section class="panel pad">${historical?.available ? `<p class="hint">분석 ${historical.included}건 · 제외 ${historical.excluded}건 · 분석 시점 ${historical.as_of}</p>${historyTable(['청산 규칙','표본','평균 순수익률','중앙값','이익 거래 비율','최저 거래','기존 대비','개선/동일/악화'],historical.performance,'historical-performance')}${historyTable(['하락 기준','해당/전체','이후 매수가 초과 관측','최종 합산 이익','최종 합산 손실'],historical.thresholds,'historical-thresholds')}${historyTable(['1차 익절','마지막 매도 단계','마지막 매도 사유','건수'],historical.handling,'historical-handling')}<p class="hint">과거 저장 가격으로 계산한 거래별 동일 가중 평균이며 계좌 수익률이 아닙니다. 신규 전향 연구와 별도입니다. 비용은 매수·매도 각 0.15%, 추가 신호는 다음 저장 가격에 체결하며 기존 최종 매도 때 종료합니다. 하락 집단은 중복되며 이익·손실 비율은 해당 집단 기준입니다. 0% 하락 집단은 선행 상승을 요구하지 않지만 0% 복귀 청산 실험은 요구합니다. 손실 종료가 모두 손절 사유라는 뜻은 아닙니다. 실제 호가 체결·관측 사이 고저가·목표가 미달 여부는 검증하지 않았습니다. 자동 주문에는 적용하지 않습니다.</p>` : '<div class="empty">과거 탐색 결과 파일이 없거나 읽을 수 없습니다.</div>'}</section>` : '';
  const targetBear = profit?.targetBear;
  const targetBearResearch = state.market === 'kr' && targetBear?.available ? `<h2 class="section-title">목표 도달 진단 · 하락장 과거 비교</h2><section class="panel pad"><p class="hint">분석 시점 ${targetBear.as_of} · 하락장 판정 불가 ${targetBear.unknown}건</p>${historyTable(['목표 도달 조건','해당/분모','비율'],targetBear.targets,'target-diagnostics')}${historyTable(['조건','마지막 매도 단계','마지막 매도 사유','건수'],targetBear.handling,'target-final-sales')}${historyTable(['하락장 규칙','표본','원래 배정자금 대비 평균 순손익','기존 대비','최저 거래 기여','개선/동일/악화'],targetBear.bears,'bear-comparisons')}<p class="hint">+10%·+20%는 고정 진단 기준이며 미확인 과거 목표 설정을 소급 확정한 값이 아닙니다. 미관측은 장중 미도달의 증명이 아닙니다. 실제 익절 여부는 체결 기록을 사용합니다. 하락장은 직전 거래일 종가·MA20이 모두 MA60 아래인 경우입니다. 매수 절반은 정수 내림하고 분할 매도는 누적 비례 내림합니다. 위 거래별 표는 다른 종목 재투자·계좌 최대낙폭·전체 현금 비중·회복 후 기회비용을 포함하지 않습니다. 계좌 비교는 별도 연구입니다. 비용은 매수·매도 각 0.15%이며 자동 매매는 변경하지 않았습니다.</p></section>` : '';
  const bearAccount = profit?.bearAccount;
  const bearAccountResearch = state.market === 'kr' && bearAccount?.available ? `<h2 class="section-title">하락장 대체 계좌 · 회복 후 20거래일</h2><section class="panel pad"><p class="hint">비교 조건: 네 계좌 공통 최대 7종목 · 기간 ${bearAccount.start}–${bearAccount.cutoff} · 분석 ${bearAccount.as_of} · 실패 실행 추천 제외 ${bearAccount.failed_signals}행</p>${historyTable(['계좌','입금 영향 제외 TWR','일별 최대낙폭','마지막 자산','평균 현금 비중','마지막 현금 비중','현금 부족 축소','보유 한도 보류'],bearAccount.rows,'bear-account-results')}${historyTable(['회복 확인','20거래일 종료','계좌','구간 TWR','구간 최대낙폭'],bearAccount.windows,'bear-account-windows')}${historyTable(['회복 확인','재진입 예산','적격 추천 행','재진입 종목','추가 물량 순손익','보류 대비 구간 종료 자산 차이'],bearAccount.opportunities,'bear-recovery-opportunity')}<p class="hint">실제 매수 기회를 고정한 계좌 재현이며 전체 매수 엔진을 재실행한 전략 백테스트가 아닙니다. 매수·매도 각 0.15% 비용과 현금 제약을 적용합니다. TWR은 입금 영향을 제외한 시간가중수익률, 최대낙폭은 일별 종가 기준입니다. 회복 재진입은 완료된 적극투자형 추천의 다음 거래일 시가에, 회복일 보류-기존 현금 차이의 1/7씩 매수해 20번째 거래일 종가에 청산하는 고정 벤치마크입니다. 기존·축소·보류·재진입 네 계좌 모두 동일한 전체 7종목 한도를 적용합니다. 보류와 재진입의 차이는 같은 한도에서 재진입 정책을 추가한 영향입니다. 추가 물량 손익과 계좌 차이는 이후 원래 매수의 제약 효과 때문에 다를 수 있습니다. 한도 없는 원장 감사는 실험 대조군과 별도입니다. 현재일 장중 자료와 실패 실행 추천은 제외했으며 실제 체결·슬리피지·기업행동 조정은 검증하지 않았습니다.</p></section>` : '';
  const dip = profit?.dips;
  const extension = profit?.extensions;
  const extensionResearch = state.market === 'kr' && extension ? `<h2 class="section-title">추가 연구 ①–④ · 수익 집중·신호 수명·체결 비용·발표 전 위험</h2><section class="panel pad">${table(['연구','상태','비교 규칙'],extension.plans.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[1]}</td><td class="reason">${x[2]}</td></tr>`),'extension-plans',15)}<p class="hint">수익 집중도: 국내 적극투자형 과거 종료 ${extension.diagnostic.complete}건 · 보유 ${extension.diagnostic.open}건 · 연결 오류 ${extension.diagnostic.errors}건 · 원본 최근 매도 ${extension.diagnostic.source_as_of}</p>${extension.diagnostic.rows.length?table(['진단','잔여 종료 거래','제외 수익 거래','순손익','전체 대비 변화'],extension.diagnostic.rows.map(x=>`<tr>${x.map(v=>`<td>${v}</td>`).join('')}</tr>`),'concentration-results',15):'<div class="empty">과거 거래 진단을 기다리고 있습니다.</div>'}${table(['수익 거래','전체 양의 손익 중 비중'],extension.diagnostic.shares.map(x=>`<tr><td>${x[0]}</td><td>${x[1]}</td></tr>`),'concentration-shares',15)}<p class="hint">추천 유효기간 관찰 시작 ${extension.start}</p>${table(['연구','등록','완료','만료 매수','기존 대비 순손익 차이','자료 부족','오류'],extension.expiry_rows.map(x=>`<tr>${x.map(v=>`<td>${v}</td>`).join('')}</tr>`),'expiry-results',15)}${table(['필요 자료','자료 대기 사유'],extension.requirements.map(x=>`<tr><td>${x[0]}</td><td class="reason">${x[1]}</td></tr>`),'extension-requirements',15)}<p class="hint">왕복 비용 0.3%를 적용해 부분 익절과 잔량 매도 손익을 합산합니다. 집중도는 과거 거래의 사후 기여 진단이며 계좌 수익률·최대낙폭이나 재투자 경로가 아닙니다. 추천 만료 비교는 등록 이후 실제 매수만 대상으로 하며 보류금은 기존 최종 매도까지 무이자 현금으로 둡니다. 저장 KOSPI 거래일 또는 추천 연결이 없으면 자료 부족으로 남깁니다. 완료된 같은 거래만 비교하며 다른 종목 재투자는 반영하지 않습니다. ③·④는 호가·예고 공시 수집을 연결했으며, 호가 체결 모형·발표 일정 검증 전 성과 계산은 보류합니다. 자동 주문은 변경하지 않습니다.</p></section>` : '';
  const dipResearch = state.market === 'kr' && dip ? `<h2 class="section-title">0%·-3%·-5% 하락 기준 독립 실험</h2><section class="panel pad">${table(['실험','추가 청산 조건'],dip.plans.map(x=>`<tr><td class="name">${x[0]}</td><td class="reason">${x[1]}</td></tr>`),'dip-plans',15)}<p class="hint">별도 시작 ${dip.start} · 신규 가상매수 ${dip.total}건 · 완료 ${dip.complete}건 · 오류 ${dip.errors}건</p>${table(['실험','완료 대조 표본','조건 발생','추가 청산 체결','평균 순수익률 차이','기존보다 유리한 건수'],dip.results.map(x=>`<tr>${x.map(v=>`<td>${v}</td>`).join('')}</tr>`),'dip-results',15)}${table(['실험','개선 손익 합계','악화 손익 합계','청산 후 관측 표본','이후 +10% 도달','이후 +20% 도달'],dip.recovery.map(x=>`<tr>${x.map(v=>`<td>${v}</td>`).join('')}</tr>`),'dip-recovery',15)}<p class="hint">세 규칙은 같은 신규 매수에서 독립 비교하며 기존 부분 익절은 유지합니다. 신호 다음 저장 가격에 잔량을 청산하고 비용은 매수·매도 각 0.15%입니다. 기존 최종 매도까지 관찰하며 오류 없는 완료 건만 집계합니다. 평균 차이는 조건 미발생을 포함한 모든 완료 건 기준입니다. 개선/악화는 기존 대비 거래 순손익 차이의 양수/음수 합이며 계좌 수익률이 아닙니다. 회복 비율은 추가 청산 이후 기존 매도까지 후속 가격이 있는 표본만 대상으로 하고 목표는 최초 매수가 대비입니다. 0% 청산은 양수 수익률 관측 이후에만 적용하며 비용·갭 때문에 본전을 보장하지 않습니다. 별도 시작 이전 거래는 소급 집계하지 않습니다.</p></section>` : '';
  const pct = value => value === '기록 없음' ? value : `${value}%`;
  const details = `<details><summary>실험 계좌 상세 결과 · ${d.experiments.length}건</summary><div>${d.experiments.length?table(['실험','적용 규칙','시작일','현재 자산','총수익률','최대낙폭','샤프지수','하락장 수익률','현금 비중','위험 상태'],d.experiments.map(x=>`<tr>${x.map((v,i)=>`<td class="${i===0?'name':i===1?'reason':''}">${[4,5,7].includes(i)?pct(v):v}</td>`).join('')}</tr>`),'experiment-details',15):'<div class="empty">실험 계좌 기록이 없습니다.</div>'}</div></details>`;
  const system = d.system || {services:[],issues:[],runs:[]};
  const serviceRows = system.services.map(x=>`<tr><td class="name">${x[0]}</td><td><span class="pill ${['정상','시작 전'].includes(x[1])?'ok':'warn'}">${x[1]}</span></td><td>${x[2]}</td></tr>`);
  const issueRows = system.issues.map(x=>`<tr><td>${x[0]}</td><td class="name">${x[1]}</td><td class="reason">${x[2]}</td></tr>`);
  const runRows = system.runs.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[1]}</td></tr>`);
  return heading('연구·상태','저장된 연구 진행 기록과 데이터·작업 상태를 확인합니다.') + historicalResearch + targetBearResearch + bearAccountResearch + profitResearch + dipResearch + extensionResearch + `<section class="panel pad"><div class="panel-head"><h2>연구 진행 현황</h2><small>${rows.length}건</small></div><div class="chips research-filters">${['전체','진행','대기','보류'].map(x=>`<button type="button" data-research-filter="${x}" aria-pressed="${state.researchFilter===x}">${x}</button>`).join('')}</div>${filtered.length ? table(['연구','현재 상태','진행 내용','확인일'],filtered.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[1]}</td><td class="reason">${x[2]}</td><td>${x[3]}</td></tr>`),'research',15) : '<div class="empty">해당 상태의 기록이 없습니다.</div>'}</section>${details}<h2 class="section-title">데이터·작업 상태</h2><section class="panel pad">${serviceRows.length?table(['항목','상태','마지막 기록'],serviceRows,'services',15):'<div class="empty">상태 기록이 없습니다.</div>'}</section><details><summary>확인할 문제 · ${system.issues.length}건</summary><div>${issueRows.length?table(['출처','항목','내용'],issueRows,'issues',15):'<div class="empty">현재 확인할 문제가 없습니다.</div>'}</div></details>${runRows.length?`<details><summary>오늘의 자동 작업 · ${runRows.length}건</summary><div>${table(['작업','상태'],runRows,'runs',15)}</div></details>`:''}`;
};

renderers.research = function () {
  if (state.market !== 'kr') return renderResearchDetails();
  const d = sample.kr, profit = d.profitResearch, cards = [], pending = [];
  const compactTable = (heads, rows) => table(heads, rows.map(row=>`<tr>${row.map(v=>`<td class="${/^[+−-][\d,.]+(?:%p?|원)?$/.test(String(v))?changeClass(v):''}">${v}</td>`).join('')}</tr>`));
  const study = (title, description, body, note='') => `<article class="panel pad research-study"><h2>${title}</h2><p class="research-description">${description}</p>${body}${note?`<p class="hint">${note}</p>`:''}</article>`;
  const historical = profit?.historical, account = profit?.bearAccount, targets = profit?.targetBear;
  if (historical?.available) cards.push(study('익절·손절 규칙 비교','기존 매도보다 수익이 얼마나 달라졌는지 비교합니다.',
    compactTable(['매도 규칙','기존 대비 평균 차이'],historical.performance.slice(1).map(r=>[r[0],r[6]])),
    `과거 ${historical.included}건 · 거래별 평균 · 계좌 수익률 아님`));
  if (account?.available) cards.push(study('하락장 매수·재진입','매수 유지, 절반 매수, 보류, 회복 후 재진입을 비교합니다.',
    compactTable(['방법','수익률','최대낙폭'],account.rows.map(r=>[r[0],r[1],r[2]])),
    `${String(account.start).replace(/^(\d{4})(\d{2})(\d{2})$/, '$1.$2.$3')}–${String(account.cutoff).replace(/^(\d{4})(\d{2})(\d{2})$/, '$1.$2.$3')} · 입금 영향 제외 · 모두 최대 7종목`));
  if (targets?.available) cards.push(study('익절 목표 도달','상승 이후 목표 도달과 하락 경로를 확인합니다.',
    compactTable(['관측 내용','해당 비율'],targets.targets.slice(0,3).map(r=>[r[0],`${r[2]} (${r[1]})`])),
    '+10%·+20% 고정 기준 · 미관측은 장중 미도달을 뜻하지 않음'));
  const extension = profit?.extensions, diagnostic = extension?.diagnostic;
  if (diagnostic?.complete) cards.push(study('수익 집중도','전체 수익이 일부 거래에 얼마나 집중됐는지 확인합니다.',
    compactTable(['항목','결과'],[
      ['종료 거래 순손익',diagnostic.rows[0]?.[3] || '기록 없음'],
      ...diagnostic.shares.map(r=>[`이익 합계 중 ${r[0]} 비중`,r[1]])]),
    `과거 종료 ${diagnostic.complete}건 · 매매 규칙의 성과 비교 아님`));
  if (profit) {
    if (profit.complete || profit.dips?.complete) {
      const results = [...profit.results.map(r=>[r[0],`${r[2]} · ${r[1]}건`]),
        ...(profit.dips?.results || []).map(r=>[r[0],`${r[4]} · ${r[1]}건`])];
      cards.push(study('신규 거래 재검증','등록 이후 새 거래로 청산 규칙을 다시 비교합니다.',compactTable(['규칙','기존 대비 평균 차이'],results)));
    } else pending.push(['신규 거래 청산 규칙 재검증',`완료 0건 · 가상매수 ${profit.total}건 관찰`]);
  }
  if (extension) {
    const expiry = extension.expiry_rows[0];
    pending.push(['추천 신호 유효기간',Number(expiry?.[2])?`완료 ${expiry[2]}건 · 기존 대비 ${expiry[4]}`:'새 거래 결과 대기']);
    pending.push(['호가 차이가 클 때 매수 보류','체결 비교 결과 대기'],['실적 발표 전 보유 축소','발표 일정·거래 결과 대기']);
  }
  for (const row of d.research || []) pending.push([row[0],row[2] || row[1]]);
  const detailsOpen = document.querySelector('.research-detail')?.open;
  return heading('연구','어떤 연구인지, 결과가 어땠는지만 먼저 확인합니다.') +
    `<div class="research-overview"><div class="research-study-grid">${cards.join('') || '<section class="panel pad empty">아직 완료된 연구 결과가 없습니다.</section>'}</div><p class="hint">과거 자료의 탐색 결과이며 자동 매매에 적용한 성과는 아닙니다.</p></div>` +
    (pending.length?`<details class="research-pending" ${cards.length?'':'open'}><summary>진행·대기 연구 ${pending.length}건</summary><div>${compactTable(['연구','현재 결과'],pending)}</div></details>`:'') +
    `<details class="research-detail" ${detailsOpen?'open':''}><summary>계산 조건·매도 내역·전체 기록 보기</summary><div>${renderResearchDetails()}</div></details>`;
};

const loadedViews = new Set();
const loadingViews = new Set();
let activeSignalKey = '';
function ensureV2View() {
  if (state.page === 'today') return true;
  const market = state.market, view = state.page;
  const key = view === 'signals' ? `${market}:${view}:${state.filter}:${state.query}:${state.paging.signals||0}` : `${market}:${view}`;
  if (view === 'signals' ? activeSignalKey === key : loadedViews.has(key)) return true;
  if (view !== 'signals' || !document.querySelector('.search'))
    document.querySelector('#content').innerHTML='<section class="panel pad"><div class="empty">화면 데이터를 불러오는 중입니다.</div></section>';
  if (loadingViews.has(key)) return false;
  loadingViews.add(key);
  const params = view === 'signals' ? `&page=${state.paging.signals||0}&filter=${encodeURIComponent(state.filter)}&q=${encodeURIComponent(state.query)}` : '';
  fetch(`/api/v2/view?market=${market}&view=${view}${params}`, {cache:'no-store'}).then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }).then(payload => {
    if (view === 'signals' && key !== `${state.market}:${state.page}:${state.filter}:${state.query}:${state.paging.signals||0}`) return;
    const real = market === 'kr' && view === 'account' ? sample.kr.accounts.find(a => a.source === 'real') : null;
    Object.assign(sample[market], escapeData(payload));
    if (real) sample.kr.accounts.push(real);
    if (view === 'signals') activeSignalKey = key;
    else loadedViews.add(key);
    if (state.market === market && state.page === view) render();
  }).catch(() => {
    if (state.market === market && state.page === view)
      document.querySelector('#content').innerHTML='<section class="panel pad"><div class="empty">화면 데이터를 불러오지 못했습니다. 탭을 다시 누르면 재시도합니다.</div></section>';
  }).finally(() => loadingViews.delete(key));
  return false;
}

if (location.protocol === 'file:') {
  document.querySelector('.demo-strip').textContent='실계좌 보호를 위해 로컬 서버 주소에서 열어 주세요.';
  document.querySelector('#content').innerHTML='<section class="panel pad"><h1>v2 대시보드 열기</h1><p>실계좌 정보는 파일 주소에서 불러오지 않습니다.</p><p><a href="http://127.0.0.1:8767/v2">로컬 v2 대시보드 열기</a></p></section>';
} else fetch('/api/v2', {cache:'no-store'}).then(response => {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}).then(payload => {
  for (const market of ['kr','us']) {
    const data = escapeData(payload[market]);
    if (!data || !Array.isArray(data.accounts) || !Array.isArray(data.signals)) throw new Error('자료 형식 오류');
    sample[market] = data;
  }
  sample.kr.accounts.push({source:'real',name:'실계좌',connected:null,reason:'',rows:[]});
  document.querySelector('.demo-strip').textContent='저장된 데이터 · 읽기 전용 · 기록이 없는 항목은 기록 없음으로 표시합니다.';
  render();
}).catch(error => {
  document.querySelector('.demo-strip').textContent='데이터 연결 안 됨';
  document.querySelector('#content').innerHTML='<section class="panel pad"><h1>데이터에 연결할 수 없습니다</h1><p>v2 서버를 시작한 뒤 아래 주소에서 대시보드를 열어 주세요.</p><p><a href="http://127.0.0.1:8767/v2">v2 대시보드 열기</a></p><p class="muted" id="load-error"></p></section>';
  document.querySelector('#load-error').textContent=error.message;
});
