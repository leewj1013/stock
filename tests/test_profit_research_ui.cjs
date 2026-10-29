const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({
  document: {addEventListener() {}, querySelector: () => ({})},
  location: {protocol: 'file:', hash: ''},
});
for (const file of ['dashboard-v2-shell.js', 'dashboard-v2-live.js']) {
  vm.runInContext(fs.readFileSync(`design/${file}`, 'utf8'), context);
}
vm.runInContext(`sample.kr = escapeData({research:[], experiments:[],
  system:{services:[],issues:[],runs:[]}, profitResearch:{
    start:'2026-10-29',total:0,complete:0,errors:0,
    plans:[['하락장 제안 ①','제안 · 미실행','현금 유지 <script>']],
    results:[['+5% 절반 익절','0','관찰 중','—']],
    dips:{start:'2026-10-29',total:0,complete:0,errors:0,
      plans:[['상승 후 0% 이하 복귀 청산','양수 관측 후 <0%'],['-3% 이하 조기 손절','-3%'],['-5% 이하 고정 손절','-5%']],
      results:[['0%','0','0','0','관찰 중','—']],recovery:[['0%','관찰 중','관찰 중','0','관찰 중','관찰 중']]}
  }});`, context);
const html = vm.runInContext('renderers.research()', context);
assert.ok(html.indexOf('하락장 대응 제안') < html.indexOf('연구 진행 현황'));
assert.match(html, /제안 · 미실행/);
assert.match(html, /현금 유지 &lt;script&gt;/);
assert.doesNotMatch(html, /현금 유지 <script>/);
assert.match(html, /신규 가상매수 0건/);
assert.match(html, /관찰 중/);
assert.match(html, /0%·-3%·-5% 하락 기준 독립 실험/);
assert.match(html, /-3% 이하 조기 손절/);
assert.match(html, /-5% 이하 고정 손절/);
assert.match(html, /양수 관측 후 &lt;0%/);
assert.ok(html.indexOf('하락장 대응 제안') < html.indexOf('하락 기준 독립 실험'));
vm.runInContext(`sample.kr.profitResearch.extensions=escapeData({start:'2026-10-29',
  diagnostic:{complete:2,open:1,errors:0,source_as_of:'2026-10-28',
    rows:[['전체 종료 거래','2','0','+100원','—']],shares:[['상위 1건','75%']]},
  plans:[['① 수익 집중도','과거 진단 갱신','사후 진단'],['② 추천 신호 유효기간','전진 관찰 등록','다음 거래일 만료'],
    ['③ 호가 차이 매수 보류','자료 대기 · 수집원 미연결','<호가>'],['④ 실적 발표 전 보유 축소','자료 대기 · 수집원 미연결','공식 예정일 필요']],
  expiry_rows:[['추천 유효기간','0','0','0','관찰 중','0','0']],
  requirements:[['호가 자료','수집원 미연결'],['발표 예정 자료','변경 이력 필요']]});`, context);
const extended=vm.runInContext('renderers.research()',context);
for(const name of ['① 수익 집중도','② 추천 신호 유효기간','③ 호가 차이 매수 보류','④ 실적 발표 전 보유 축소']) assert.ok(extended.includes(name));
assert.match(extended,/자료 대기 · 수집원 미연결/);
assert.match(extended,/&lt;호가&gt;/);
assert.ok(extended.indexOf('하락장 대응 제안') < extended.indexOf('추가 연구 ①–④'));
console.log('profit research order, empty state, escaping: OK');
vm.runInContext(`sample.kr.profitResearch.historical=escapeData({available:true,included:48,excluded:3,as_of:'2026-10-29',performance:[['<규칙>','48','+0.703%','-1%','29%','-6%','+0.665%p','12/32/4']],thresholds:[['-5% 이하','17/48','3','0','17']],handling:[['체결','final','손절','5']]});`,context);
const historical=vm.runInContext('renderers.research()',context);
assert.match(historical,/분석 48건/);
assert.match(historical,/\+0.665%p/);
assert.match(historical,/&lt;규칙&gt;/);
assert.doesNotMatch(historical,/<규칙>/);
assert.ok(historical.indexOf('현재 자료 실험 결과') < historical.indexOf('하락장 대응 제안'));
vm.runInContext(`sample.kr.profitResearch.targetBear=escapeData({available:true,as_of:'2026-10-29',unknown:0,targets:[['1차 익절 후 +20% 도달 미관측','5/14','35.7%']],handling:[['조건','final','<매도>','5']],bears:[['신규 매수 수량 절반','36','+0.211%','+0.134%p','-6.672%','26/0/10']]});`,context);
const targetBear=vm.runInContext('renderers.research()',context);
assert.match(targetBear,/목표 도달 진단 · 하락장 과거 비교/);
assert.match(targetBear,/5\/14/);
assert.match(targetBear,/&lt;매도&gt;/);
assert.match(targetBear,/계좌 최대낙폭/);
vm.runInContext(`sample.kr.profitResearch.bearAccount=escapeData({available:true,start:'20260826',cutoff:'20261028',as_of:'2026-10-29',failed_signals:16,rows:[['<기존 계좌>','+4.688%','-4.922%','104,567,550원','48.2%','73.1%','1','0']],windows:[['20260921','20261023','보류','+3.077%','-4.522%']],opportunities:[['20260921','35,816,589원','269','7','-981,498원','-4,058,451원']]});`,context);
const accountResearch=vm.runInContext('renderers.research()',context);
assert.match(accountResearch,/하락장 대체 계좌 · 회복 후 20거래일/);
assert.match(accountResearch,/입금 영향 제외 TWR/);
assert.match(accountResearch,/&lt;기존 계좌&gt;/);
assert.match(accountResearch,/-981,498원/);
assert.match(accountResearch,/실패 실행 추천 제외 16행/);
assert.match(accountResearch,/네 계좌 모두 동일한 전체 7종목 한도/);
assert.doesNotMatch(accountResearch,/다른 계좌의 과거 보유 수는 그대로 유지/);
