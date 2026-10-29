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
const run = source => vm.runInContext(source, context);
for (const [value, expected] of [['+2.5%', 'positive'], ['-2.5%', 'negative'], ['2.5%', 'positive'], ['−2.5%', 'negative'], ['+0.00%', ''], ['기록 없음', ''], ['—', '']]) {
  assert.equal(run(`changeClass(${JSON.stringify(value)})`), expected);
}
assert.match(run("metric('수익률', '-2.5%')"), /class="negative"/);
assert.doesNotMatch(run("metric('총자산', '₩1,000')"), /class="positive"/);
const aligned = run(`table(['종목','추천일','수량','수익률','상태'], ['<tr><td class="name">종목</td><td>2026-10-28</td><td>10</td><td class="positive">+2%</td><td>추적 중</td></tr>'])`);
assert.match(aligned, /<th scope="col" data-numeric>수량<\/th>/);
assert.match(aligned, /<td data-numeric>10<\/td>/);
assert.match(aligned, /<td data-numeric class="positive">\+2%<\/td>/);
assert.match(aligned, /<td>2026-10-28<\/td>/);
assert.match(aligned, /<td>추적 중<\/td>/);
run(`sample.kr = {day:'2026-10-27', accounts:[{
  name:'계좌', kind:'가상매매', asset:'₩1,000', cash:'₩100', daily:'₩+10', returnPct:'-2.00%', hold:'1종목',
  priceAsOf:'2026-10-27', priceIssues:[], realized:'₩+10',
  rows:[['종목','1','10%','1일','₩100','₩98','2026-10-27','₩-2','-2.00%','정상','₩95','₩99']],
  sales:[['2026-10-26','종목','전량매도','1','₩110','₩+10','+10.00%','익절']]
}], experiments:[['실험','','2026-10-20','₩1,000','2.00','-1.00','1.2','','','정상']],
compareChart:{days:['2026-10-26','2026-10-27'],series:[{label:'계좌',color:'#000',values:[0,-2]}]}}`);
const account = run('renderers.account()');
assert.match(account, /총자산과 종목별 손익은 같은 저장가격/);
assert.match(account, /장전 조회 대기/);
assert.doesNotMatch(account, /총자산은 별도 계좌 평가 기록/);
assert.match(account, /<td data-numeric class="negative">-2.00%<\/td>/);
assert.match(account, /<td data-numeric class="positive">\+10.00%<\/td>/);
const compare = run('renderers.compare()');
assert.match(compare, /<td data-numeric class="negative">-2.00%<\/td>/);
assert.match(compare, /<td data-numeric class="positive">2.00%<\/td>/);
assert.match(compare, /<b class="negative">-2.00%<\/b>/);
run(`sample.kr.accounts[0] = {source:'real',name:'실계좌',connected:true,asOf:'2026-10-27',asset:'₩1,000',cash:'₩100',unrealized:'₩-2',returnPct:'-2.00%',hold:'1종목',rows:[['종목','1','₩100','₩98','₩98','₩-2','-2.00%','정상']]}`);
assert.match(run('renderers.account()'), /<td data-numeric class="negative">-2.00%<\/td>/);
run(`state.market='us'; sample.us={day:'2026-10-27',accounts:[{name:'미국',kind:'가상매매',asset:'$100',cash:'$10',daily:'$+1',returnPct:'+2.00%',hold:'1종목',rows:[['종목','10%','—','+2.00%','보유 중']]}]}`);
assert.match(run('renderers.account()'), /<td data-numeric class="positive">\+2.00%<\/td>/);
console.log('v2 return colors: OK');
