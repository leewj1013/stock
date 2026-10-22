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
  const headers = domestic ? (sellOnly ? ['매도 신호일','종목','신호','매도 알림가','수익률','상태','사유'] : ['추천일','종목','신호','추천가','현재가','매도 신호일','매도 알림가','수익률','상태','이유']) : [sellOnly?'전략 매도일':'발생일','종목','신호','결정가','상태','이유','전략'];
  const body = rows.map(x => {
    const sell = x.signal.includes('매도'), detail = x.detail || {};
    const date = x.date.replace('T',' ').slice(0,16);
    if (domestic && sellOnly) return `<tr><td>${cell(detail.sellDate || date)}</td>${name(x)}<td>${badge(x.signal)}</td><td>${cell(detail.sellPrice)}</td>${profit(x)}<td>${x.status}</td><td class="reason">${x.reason}</td></tr>`;
    if (domestic) return `<tr><td>${sell?'—':date}</td>${name(x)}<td>${badge(x.signal)}</td><td>${cell(detail.entryPrice)}</td><td>${cell(detail.currentPrice)}</td><td>${cell(detail.sellDate || (sell ? date : ''))}</td><td>${cell(detail.sellPrice)}</td>${profit(x)}<td>${x.status}</td><td class="reason">${x.reason}</td></tr>`;
    return `<tr><td>${date}</td>${name(x)}<td>${badge(x.signal)}</td><td>${cell(detail.decisionPrice)}</td><td>${x.status}</td><td class="reason">${x.reason}</td><td>${x.source}</td></tr>`;
  });
  const pager = pages > 1 ? `<div class="pager"><span>${page*20+1}–${Math.min((page+1)*20,total)} / ${total}건</span>${[['«',0],['‹',Math.max(0,page-1)]].map(([label,n])=>`<button type="button" data-table-page="signals:${n}" ${page===0?'disabled':''} aria-label="${label==='«'?'첫':'이전'} 페이지">${label}</button>`).join('')}${Array.from({length:Math.min(5,pages)},(_,i)=>Math.floor(page/5)*5+i).filter(n=>n<pages).map(n=>`<button type="button" data-table-page="signals:${n}" class="${n===page?'active':''}" ${n===page?'aria-current="page"':''}>${n+1}</button>`).join('')}${[['›',Math.min(pages-1,page+1)],['»',pages-1]].map(([label,n])=>`<button type="button" data-table-page="signals:${n}" ${page===pages-1?'disabled':''} aria-label="${label==='»'?'마지막':'다음'} 페이지">${label}</button>`).join('')}</div>` : '';
  return heading('추천·신호','지금까지 저장된 국내 추천·매도 알림과 미국 전략 결정 기록입니다.') + `<section class="panel pad signal-grid"><div class="toolbar"><div class="chips">${['전체','매수 추천','매도 신호','추적 중'].map(x=>`<button type="button" data-filter="${x}" aria-pressed="${state.filter===x}">${x}</button>`).join('')}</div><input class="search" type="search" aria-label="종목 검색" placeholder="종목·이유 검색"></div><p class="hint">현재 조건 ${total}건 · 최근 기록부터 표시${domestic?' · 현재가는 마지막 저장 추적가입니다. 매도 신호일·알림가는 실제 체결 기록이 아닙니다.':''}</p>${body.length ? table(headers,body) + pager : '<div class="empty">해당 조건의 저장 기록이 없습니다.</div>'}</section>`;
};

function comparisonChart() {
  const data = sample[state.market].compareChart || {days:[],series:[]};
  if (data.days.length < 2 || !data.series.length) return '<div class="empty">비교할 공통 거래일 기록이 부족합니다.</div>';
  const all = data.series.flatMap(s=>s.values).concat(0), low = Math.min(...all), high = Math.max(...all), span = high-low || 1;
  const y = v => 20+(high-v)/span*170;
  const grid = [0,1,2,3].map(i=>{const value=high-span*i/3, pos=y(value).toFixed(1);return `<path d="M52 ${pos}H420" stroke="#e5eaed" stroke-dasharray="2 3"/>`}).join('');
  const labels = [0,1,2,3].map(i=>{const value=high-span*i/3;return `<span class="axis-y axis-${i}">${value.toFixed(1)}%</span>`}).join('');
  const lines = data.series.map(s=>`<polyline points="${s.values.map((v,i)=>`${(52+i*360/(data.days.length-1)).toFixed(1)},${y(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`).join('');
  const legend = data.series.map((s,i)=>`<span><i class="series-${i%3}"></i>${s.label} <b>${s.values.at(-1).toFixed(2)}%</b></span>`).join('');
  return `<div class="chart-key">${legend}</div><div class="chart-frame compare-frame"><svg class="compare-chart" viewBox="0 0 430 225" preserveAspectRatio="none" role="img" aria-label="${data.days[0]}부터 ${data.days.at(-1)}까지 공통 거래일 누적 수익률 비교">${grid}${lines}</svg>${labels}<span class="axis-x axis-start">${data.days[0].slice(5)}</span><span class="axis-x axis-end">${data.days.at(-1).slice(5)}</span></div>`;
}

renderers.compare = function () {
  const d = sample[state.market];
  const pct = value => value === '기록 없음' ? value : `${value}%`;
  const experiments = `<h2 class="section-title">관찰용 실험 계좌 · 위험지표</h2><section class="panel pad">${d.experiments.length?table(['실험 계좌','시작일','현재 자산','총수익률','최대낙폭','샤프지수','상태'],d.experiments.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[2]}</td><td>${x[3]}</td><td>${pct(x[4])}</td><td>${pct(x[5])}</td><td>${x[6]}</td><td>${x[9]}</td></tr>`),'experiments',15):'<div class="empty">실험 계좌 기록이 없습니다.</div>'}</section><p class="hint">관찰 전용 계좌입니다. 시작일이 다르면 총수익률을 직접 비교하지 마세요. 미국 최대낙폭은 저장된 첫 평가일 이후 기준이며 샤프지수는 수익률 기록이 20개 이상일 때 표시합니다.</p>`;
  return heading('성과 비교','공통 거래일을 시작점 0%로 맞춰 계좌와 지수를 비교합니다.') + `<section class="panel pad"><div class="panel-head"><h2>같은 기간의 누적 수익률</h2><small>${d.compareChart.days.length}거래일</small></div>${comparisonChart()}</section><h2 class="section-title">계좌별 현재 기록</h2><section class="panel pad">${table(['계좌','총자산',state.market==='kr'?'입금액 대비 수익률':'누적 수익률','최근 평가 변화'],d.accounts.filter(a=>a.source!=='real').map(a=>`<tr><td class="name">${a.name}</td><td>${a.asset}</td><td>${a.returnPct}</td><td>${a.daily}</td></tr>`))}</section>${experiments}<div class="footer-note">차트는 입금 영향을 제거한 국내 가상계좌 수익률과 미국 전략·SPY 장부의 공통 날짜만 사용합니다.</div>`;
};

renderers.account = function () {
  const d = sample[state.market], a = d.accounts[state.account] || d.accounts[0];
  const choices = `<div class="account-choice">${d.accounts.map((x,i)=>`<button type="button" data-account="${i}" aria-pressed="${state.account===i}">${x.name}</button>`).join('')}</div>`;
  if (a.source !== 'real') {
    const domestic = state.market === 'kr';
    const headers = domestic ? ['종목','수량','보유 비중','보유일수','평균매수가','마지막 점검가','가격 점검 시각','평가손익','수익률','매도 감시상태','손절 기준','20일선'] : ['종목','보유 비중','평가손익','수익률','상태'];
    return heading('계좌','계좌별 자산과 보유종목의 저장 기록을 확인합니다.') + choices + `<div class="account-info"><b>${a.name}</b> · ${a.kind}</div><div class="wide-metrics">${metric('총자산',a.asset)}${metric('최근 평가 변화',a.daily)}${metric('현금',a.cash)}${metric(domestic?'입금액 대비 수익률':'누적 수익률',a.returnPct)}</div><h2 class="section-title">현재 보유종목 · ${a.hold}</h2><section class="panel pad">${a.rows.length ? table(headers,a.rows.map(row=>`<tr>${row.map((cell,i)=>`<td class="${i===0?'name':''}">${cell}</td>`).join('')}</tr>`),'holdings',15) : '<div class="empty">보유종목 기록이 없습니다.</div>'}</section>${domestic?`<p class="hint">마지막 종목 점검 ${a.priceAsOf} · 가격 확인 필요 ${a.priceIssues.length}종목${a.priceIssues.length?` (${a.priceIssues.join(', ')})`:''} · 종목별 점검 시각은 다를 수 있습니다. 점검가가 없으면 평가손익과 수익률을 표시하지 않습니다. 위 총자산은 별도 계좌 평가 기록을 사용합니다.</p><h2 class="section-title">매도 내역 · 누적 실현손익 ${a.realized}</h2><section class="panel pad">${a.sales.length?table(['매도일','종목','구분','수량','매도가','실현손익','수익률','사유'],a.sales.map(row=>`<tr>${row.map((cell,i)=>`<td class="${i===1?'name':i===7?'reason':''}">${cell}</td>`).join('')}</tr>`),'sales',15):'<div class="empty">저장된 매도 내역이 없습니다.</div>'}</section>`:''}`;
  }
  const intro = heading('계좌','토스증권 국내주식 실계좌를 읽기 전용으로 조회합니다.') + choices;
  if (a.connected === null) return intro + '<section class="panel pad"><div class="empty">실계좌를 조회하고 있습니다.</div></section>';
  if (!a.connected) return intro + `<section class="panel pad"><div class="panel-head"><h2>실계좌 연결 안 됨</h2><button type="button" data-refresh-real="1">다시 조회</button></div><div class="empty">${a.reason}</div></section>`;
  return intro + `<div class="account-info"><b>실계좌 연결됨</b> · 국내주식만 표시 · 조회 시각 ${a.asOf} <button type="button" data-refresh-real="1">새로고침</button></div><div class="wide-metrics">${metric('총자산',a.asset)}${metric('주문 가능 현금',a.cash)}${metric('평가손익',a.unrealized)}${metric('보유주식 평가수익률',a.returnPct)}</div><h2 class="section-title">현재 보유종목 · ${a.hold}</h2><section class="panel pad">${a.rows.length ? table(['종목','수량','평균매수가','현재가','평가액','평가손익','수익률','상태'],a.rows.map(row=>`<tr>${row.map((cell,i)=>`<td class="${i===0?'name':''}">${cell}</td>`).join('')}</tr>`),'real-holdings',15) : '<div class="empty">보유 중인 국내주식이 없습니다.</div>'}</section><p class="hint">실계좌 수익률은 현재 보유 중인 국내주식의 평가손익 ÷ 매입원가입니다. 현금과 과거 매도 손익은 포함하지 않습니다.</p>`;
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

renderers.research = function () {
  const d = sample[state.market], rows = d.research || [], filtered = rows.filter(x => state.researchFilter === '전체' || x[4] === state.researchFilter);
  const pct = value => value === '기록 없음' ? value : `${value}%`;
  const details = `<details><summary>실험 계좌 상세 결과 · ${d.experiments.length}건</summary><div>${d.experiments.length?table(['실험','적용 규칙','시작일','현재 자산','총수익률','최대낙폭','샤프지수','하락장 수익률','현금 비중','위험 상태'],d.experiments.map(x=>`<tr>${x.map((v,i)=>`<td class="${i===0?'name':i===1?'reason':''}">${[4,5,7].includes(i)?pct(v):v}</td>`).join('')}</tr>`),'experiment-details',15):'<div class="empty">실험 계좌 기록이 없습니다.</div>'}</div></details>`;
  const system = d.system || {services:[],issues:[],runs:[]};
  const serviceRows = system.services.map(x=>`<tr><td class="name">${x[0]}</td><td><span class="pill ${['정상','시작 전'].includes(x[1])?'ok':'warn'}">${x[1]}</span></td><td>${x[2]}</td></tr>`);
  const issueRows = system.issues.map(x=>`<tr><td>${x[0]}</td><td class="name">${x[1]}</td><td class="reason">${x[2]}</td></tr>`);
  const runRows = system.runs.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[1]}</td></tr>`);
  return heading('연구·상태','저장된 연구 진행 기록과 데이터·작업 상태를 확인합니다.') + `<section class="panel pad"><div class="panel-head"><h2>연구 진행 현황</h2><small>${rows.length}건</small></div><div class="chips research-filters">${['전체','진행','대기','보류'].map(x=>`<button type="button" data-research-filter="${x}" aria-pressed="${state.researchFilter===x}">${x}</button>`).join('')}</div>${filtered.length ? table(['연구','현재 상태','진행 내용','확인일'],filtered.map(x=>`<tr><td class="name">${x[0]}</td><td>${x[1]}</td><td class="reason">${x[2]}</td><td>${x[3]}</td></tr>`),'research',15) : '<div class="empty">해당 상태의 기록이 없습니다.</div>'}</section>${details}<h2 class="section-title">데이터·작업 상태</h2><section class="panel pad">${serviceRows.length?table(['항목','상태','마지막 기록'],serviceRows,'services',15):'<div class="empty">상태 기록이 없습니다.</div>'}</section><details><summary>확인할 문제 · ${system.issues.length}건</summary><div>${issueRows.length?table(['출처','항목','내용'],issueRows,'issues',15):'<div class="empty">현재 확인할 문제가 없습니다.</div>'}</div></details>${runRows.length?`<details><summary>오늘의 자동 작업 · ${runRows.length}건</summary><div>${table(['작업','상태'],runRows,'runs',15)}</div></details>`:''}`;
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
