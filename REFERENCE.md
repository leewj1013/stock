# 참고자료

전략 손잡이(knob)를 **왜 그 값으로 두는지** 판단하는 데 필요한 개념과 자료를 모은다.

난이도순(기초→심화)이 아니라 **코드에 답 없이 박혀 있는 손잡이순**으로 정리한다. 자료가 필요해지는 순간은 "심화 3번이 뭐였지?"가 아니라 "`MIN_MARKET_UP_RATIO`를 0.45로 둔 게 맞나?"이기 때문이다.

## 문서 역할 구분

| 문서 | 역할 |
|---|---|
| `README.md` §주요 설정값 | 손잡이가 **무엇**이고 현재 값이 얼마인지 |
| `STRATEGY_NOTES.md` | 아직 **구현 안 한** 전략 후보 대기열 |
| **이 문서** | 그 값을 **왜** 그렇게 정해야 하는지 판단할 개념과 자료 |

같은 내용을 옮겨 적지 않는다. 설정값 설명이 필요하면 README를 고친다.

## 운용 규칙

1. **수집은 상시, 적용은 표본 충족 후.** 현재 20일 성숙 표본 31건 / 게이트 300건 (성숙 대기 중인 유효 픽 157건). 표본이 얇을 때 새 기법을 얹으면 검증이 아니라 과적합이다. 뉴스 신호에서 이미 겪었다 — 어떤 구간에서도 예측력이 없었고, 그건 자료가 부족해서가 아니라 신호가 약해서였다. 대기 중 아이디어를 아래 섹션에 쌓아두면 표본이 찼을 때 검증 후보 목록이 준비된 상태가 된다.
2. **예외: §1 검증 방법론은 지금 적용한다.** 표본 수와 무관하다. 오히려 표본이 차기 **전에** 정리돼 있어야 첫 승격 판단을 그르치지 않는다.

## 항목 형식

```markdown
### <개념 이름>
- 개념: 한 줄 요약
- 걸리는 곳: `파일:라인` — 관련 손잡이와 현재 값
- 적용 시 변화: 반영하면 코드/기준이 어떻게 바뀌는지
- 자료: 링크 또는 저자·제목
```

---

## 1. 검증 방법론

담당 손잡이: `LEARNING_SIGNIFICANCE_LEVEL=0.05`, `LEARNING_MIN_SAMPLES=300`, `LEARNING_VALIDATION_FOLDS=3`, `LEARNING_VALIDATION_MIN_SAMPLES=60`
코드 위치: `stock_alarm/strategy_learning.py:258-263` (승격 판정), `:277` (표본 게이트), `stock_alarm/data_store.py:941` (유효표본 필터)

### 다중검정 보정
- 개념: 여러 가중치 조합을 시도한 뒤 가장 좋은 하나의 p-value를 그대로 쓰면, 그 값은 실제보다 낙관적이다. 20개 조합을 시도하면 진짜 효과가 없어도 하나쯤은 p<0.05를 우연히 통과한다.
- 걸리는 곳: `strategy_learning.py:258-263` — `p_value < alpha`(0.05) 단일 기준. 제안 가중치는 탐색으로 구해지는데, 탐색 횟수가 유의성 판정에 반영되지 않는다.
- 적용 시 변화: 유효 alpha를 시도 횟수로 나누거나(Bonferroni), 승격 문턱을 t>3.0 수준으로 올린다. 승격이 지금보다 확실히 어려워진다 — 그게 목적이다.
- 자료:
  - [Harvey, Liu & Zhu, "...and the Cross-Section of Expected Returns" (RFS 2016)](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824) — 발표된 팩터 316개를 세고, t>2.0은 너무 무르니 신규 팩터는 **t>3.0**을 요구해야 한다고 결론. ([NBER 워킹페이퍼 무료본](https://www.nber.org/papers/w20592))
  - [Bailey & López de Prado, "The Deflated Sharpe Ratio" (JPM 2014)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) — 시도 횟수와 비정규성을 함께 보정한 Sharpe. ([Bailey 외, "Statistical Overfitting and Backtest Performance" 무료 PDF](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf))

### 워크포워드 폴드 설계 (purging·embargo)
- 개념: 학습구간과 검증구간이 시간축에서 겹치거나 인접하면 정보가 새어 성과가 부풀려진다. 보유기간만큼 폴드 경계를 잘라내고(purge) 완충구간을 둬야(embargo) 한다.
- 걸리는 곳: `strategy_learning.py:277-283` — 3폴드, 폴드당 최소 60건. 목적함수가 최장 10일 수익률을 쓰므로 폴드 경계 근처 표본은 검증구간과 기간이 겹친다.
- 적용 시 변화: 폴드 경계에서 최소 10거래일분 표본을 버린다. 유효 표본이 줄어 승격 시점이 밀린다.
- 자료:
  - [Purged K-Fold: Why Standard Cross-Validation Breaks in Finance](https://ariaanalyst.pro/blog/purgedkfold-financial-ml) — 개념 요약, 가장 짧게 읽을 수 있는 출발점
  - [Purged & Embargoed Cross-Validation, Explained](https://quantmemo.com/concepts/purged-embargoed-cv) — 왜 표준 k-fold가 금융에서 새는지 (라벨 구간 중첩 → IID 가정 붕괴)
  - López de Prado, "Advances in Financial Machine Learning" (2018) **ch.7 Cross-Validation in Finance** — purged K-fold 원전, CPCV까지

### [적용됨 2026-09-10] 라이브 승격 게이트가 Mann-Whitney → Newey-West HAC로 교체됨

이 저장소에 이미 `newey_west_mean_test`(Bartlett HAC, `statistical_validation.py:65`)와 `benjamini_hochberg`(`statistical_validation.py:98`)가 구현돼 있었고, `benchmark_comparison.py`는 둘 다 쓰는데 **라이브 승격 게이트(`strategy_learning.py`)만 쓰지 않았다.**

문제: `objective()`가 1/3/5/10일 수익률을 섞어 쓰므로, 10거래일 이내에 나온 픽들은 수익률 계산 구간이 겹쳐 자기상관이 생긴다. 기존 `return_distribution_p_value`는 Mann-Whitney U 검정으로 관측이 독립이라고 가정했다 — purging이 막는 "정보 누출"과는 다른 문제로, 순서를 섞어도 사라지지 않는다.

**조치**: `return_distribution_p_value`를 Newey-West HAC 기반 이표본 z검정으로 교체했다(시그니처는 그대로라 `profile_weight_learning.py`·`profile_weight_validation.py`·`weight_variant_backtest.py`의 기존 호출부는 무수정으로 함께 개선됨). lag는 `RETURN_HAC_LAG=9`(최장 horizon 10일 − 1, `newey_west_mean_test` 자체 docstring의 관례). `_ranked_returns`가 선택된 표본을 점수순이 아니라 **날짜순**으로 반환하도록 함께 고쳤다 — HAC의 lag 구조는 "가까운 원소가 실제로 시간상 가까울 때"만 의미가 있다.

**검증**: AR(1) 자기상관을 주입한 합성 데이터(각 군 60건, 200회 반복)로 두 검정의 오탐률을 비교.

| | 실제 차이 없음일 때 오탐률(명목 5%) | 약한 신호(평균차 0.15) 검출률 |
|---|---|---|
| Mann-Whitney (구) | **22.0%** — 명목의 4배 이상 | 28.5% |
| Newey-West HAC (신) | **12.5%** | 12.5% |

개선은 뚜렷하지만 완전하지 않다 — 12.5%도 명목 5%보다 여전히 높다. Bartlett 커널과 고정 lag는 근사치이고, 검증 폴드가 60건 수준으로 작아 HAC 표준오차 추정 자체에도 잡음이 있다. **이 검정을 표본이 늘어도 재검증 없이 무한정 신뢰하면 안 된다** — §1 "표본크기와 검출력" 항목과 함께 본다.

**적용 안 한 것**: `benjamini_hochberg`는 붙이지 않았다. `learn()`은 폴드마다 같은 가중치 제안 하나를 반복 검증하는 구조라 folds가 "여러 가설"이 아니라 "한 가설의 강건성 확인"에 가깝고, 이미 전 폴드 통과를 요구하는 게 FDR보다 보수적인 기준이다. FDR이 실제로 맞는 자리는 여러 파라미터 변형을 비교하는 `weight_variant_backtest.py`인데, 거기는 아직 안 쓴다 — 별도 판단 필요.

### Look-ahead·생존편향
- 개념: 백테스트 시점에 알 수 없었던 정보(사후 수정된 재무제표, 상장폐지로 사라진 종목)가 들어가면 실전에서 재현되지 않는 성과가 나온다.
- 걸리는 곳: `stock_alarm/point_in_time_store.py` — PIT 저장소로 이미 대응 중. 다만 종목 유니버스가 현재 상장 종목 기준이면 생존편향은 남는다.
- 적용 시 변화: 유니버스 구성 시점을 과거로 고정해야 한다. 상폐 종목 데이터가 없으면 백테스트 수익률은 상방 편향으로 읽는다. 참고로 상폐 종목 제외는 연수익률을 **1~4%p** 부풀린다고 알려져 있다.
- 자료:
  - [The Seven Sins of Quantitative Investing (Portfolio Optimization Book §8.2)](https://portfoliooptimizationbook.com/book/8.2-seven-sins.html) — 백테스트 7대 함정을 한자리에, 이 중 생존편향·look-ahead가 1·2번
  - [Problems in Backtesting and Biases in Data (CFA Level 2 정리)](https://analystprep.com/study-notes/cfa-level-2/problems-in-backtesting/) — 교과서식 정의
  - [Survivorship Bias in Backtesting Explained](https://www.luxalgo.com/blog/survivorship-bias-in-backtesting-explained/) — 편향 크기의 실증 수치

### 표본크기와 검출력
- 개념: 검출하려는 엣지가 작을수록 필요한 표본은 제곱으로 늘어난다. 수익률 표준편차 대비 엣지가 작으면 300건으로는 아주 큰 효과만 잡힌다.
- 걸리는 곳: `strategy_learning.py:277` — `max(300, LEARNING_MIN_SAMPLES)`. 300은 최소선이지 충분선이 아니고, 근거도 없는 숫자다.
- 적용 시 변화: 첫 승격 시도가 rejected로 나와도 "가중치가 틀렸다"가 아니라 "표본이 부족하다"일 수 있음을 구분하게 된다. 사전에 검출 가능한 최소 효과크기를 계산해두면 판단이 갈린다. MinTRL을 먼저 계산하면 **300이 충분한 수인지 자체를 검산**할 수 있다.
- 자료:
  - [Probabilistic Sharpe Ratio와 Minimum Track Record Length](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/) — "이 Sharpe가 기준치 위라고 말하려면 표본이 몇 개 필요한가"에 답하는 공식
  - [López de Prado, "Deflating the Sharpe Ratio" 슬라이드 (PDF)](http://boston.qwafafew.org/wp-content/uploads/sites/4/2017/01/Lopez_de_Prado_Sharpe.pdf) — MinTRL·Minimum Backtest Length를 그림으로
  - [jsharpe (파이썬 구현)](https://github.com/tschm/jsharpe) — PSR·MinTRL·FWER/FDR 보정. 직접 계산해볼 때

### [해결됨 2026-09-10] 표본 정의 = 20거래일 성숙 300건

"300건"이 무엇의 300건인지 모호했다. `README.md`가 "1·3·5·10·20거래일 성과를 DB에 누적"과 "유효 표본 300건 이상"을 나란히 적었지만, `objective()`는 20일을 쓰지 않고 1/3/5/10일 중 **하나만 채워져도** 표본 1건으로 셌다.

**결정: 20거래일 성과가 확정된 추천만 표본으로 센다.** (`strategy_learning.py:learn()`에서 `return_20d_pct` 없는 행을 제외)

- `objective()` 자체는 그대로 1/3/5/10일을 평균한다 — `validation_backtest`의 `_learning_row`(`validation_backtest.py:276`)가 20일 수익률을 아예 계산하지 않아, 여기에 20일 요건을 넣으면 백테스트가 전부 `insufficient_data`로 죽는다.
- 현재 위치: **31/300**.

### [해결됨 2026-09-10] 전략 버전 승격 시 표본 중복 계상

`recommendation_outcomes`의 PK가 `(pick_date, ticker, strategy_version)`인데 `sync_outcomes`가 모든 행에 **현재** 버전을 찍는다(`strategy_learning.py:87`). v5→v6 때 PK가 달라져 REPLACE가 INSERT가 되고, 갱신이 끊긴 v5 행이 고아로 남아 **같은 픽이 두 번 세어졌다**.

- 발견 당시 규모: 301행 중 고유 픽 205건, 중복 96행. usable 203건 중 46건이 중복.
- 영향: 표본 수 과대계상 + 비모수 검정이 한 관측을 둘로 취급 → **p-value 낙관 편향**. §1 다중검정 문제와 별개인 더 기초적인 결함이었다.
- 수정: `upsert_recommendation_outcomes`가 같은 픽의 다른 버전 행을 삭제한다(`data_store.py`). 기존 96행은 일회성 정리 완료(백업 `data/backups/stock_alarm-pre-dedupe-20260910-134302.db`).
- 정리 후 usable 203 → **157**.

### [미결] 백테스트가 새 게이트를 재현하지 못함

라이브 게이트는 20일 성숙을 요구하는데 `validation_backtest`는 1~10일만 계산한다. 즉 **백테스트가 검증하는 대상과 라이브가 승격시키는 대상이 다르다.** `_learning_row`의 horizon에 20을 추가하면 맞출 수 있지만 백테스트 목적함수 값 자체가 바뀌므로 별도 판단이 필요하다.

### [해결됨 2026-09-10] 최소표본 환경변수 이원화

대시보드가 `STRATEGY_LEARNING_MIN_SAMPLES`(`dashboard.py:670`), 실제 게이트가 `LEARNING_MIN_SAMPLES`(`strategy_learning.py:277`)를 읽고 있었다. 전자는 `.env`·`.env.example` 어디에도 없어 항상 300으로 폴백했으므로, `LEARNING_MIN_SAMPLES`를 바꾸면 대시보드만 옛 문턱을 계속 표시했을 것이다. 대시보드가 같은 변수를 읽도록 통일했다.

참고로 대시보드는 처음부터 `completed_20d`를 "20일 학습 표본"으로 세고 있었다(`dashboard.py:668-681`). **20일 성숙이 원래 의도였고 학습 게이트만 그걸 반영하지 않았다는 증거다.**

### [검증 완료 2026-09-10] 후보군 확장의 실제 크기 = 2.14배 (자릿수 아님)

비선택 후보까지 학습 표본에 넣으면 표본이 자릿수 단위로 늘 것으로 봤으나, **틀렸다.**

`candidate_snapshots` 294,386행 중 **팩터 점수가 있는 행은 7,482행뿐**이고, 그마저 5분마다 재평가된 중복이라 고유 (일자, 종목)으로는 **414건**이다. 나머지 28만 행은 필수 필터(`volume_ratio`, `below_ma20`, `trading_value` 등)에서 점수 계산 전에 걸러진 것이라 학습에 쓸 팩터값이 아예 없다.

| 항목 | 건수 |
|---|---|
| 점수 보유 고유 후보 | 414 |
| 이미 추적 중인 픽과 겹침 | 180 |
| **신규로 얻는 관측** | **234** |
| 합산 표본 | 439 (현재 205 대비 **2.14배**) |
| 신규 관측 중 20일 성숙분 | **17** |

즉 지금 구현해도 즉시 얻는 건 17건(31 → 48)뿐이다. 2.14배는 앞으로의 **축적 속도**에 적용된다.

다만 개수보다 중요한 이유가 따로 있다: `_ranked_returns`(`strategy_learning.py:185`)는 표본을 제안 가중치로 재정렬해 **상위 절반**의 수익률을 비교한다. 표본이 이미 선택된 픽뿐이면 "엘리트 집합의 상위 절반"을 고르는 셈이라 가중치 변화가 만들어낼 수 있는 차이가 거의 없다. 비선택 후보가 섞여야 재정렬이 실제 선택 과제를 닮는다.

**보류 사유**: 실행하려면 (a) 중복 평가 대표행 선택 규칙, (b) 비선택 후보의 사후 수익률 백필, (c) `recommendation_outcomes` 스키마 변경 또는 별도 테이블, (d) `learn()`이 무엇을 학습 표본으로 볼지 재정의가 필요하다. 실계좌가 걸린 승격 로직을 바꾸는 일이라 별도 판단이 필요하다. `candidate_snapshots`는 365일 보존(`db_maintenance.py:36`, `DB_RAW_RETENTION_DAYS`)이고 가격은 사후 조회가 가능하므로 **미뤄도 데이터는 잃지 않는다.**

---

## 2. 시장 레짐 필터

담당 손잡이: `MIN_MARKET_UP_RATIO=0.45`
코드 위치: `stock_alarm/app.py:643-645` (판정), `:775`·`:897` (적용 — 미달 시 개별 종목 평가 자체를 건너뛴다)

### 필터 임계값의 기회비용
- 개념: 하락장 진입을 막는 필터는 손실도 막지만 반등 초입도 함께 버린다. 임계값은 "얼마나 막느냐"가 아니라 "막아서 피한 손실 − 놓친 수익"으로 평가해야 한다.
- 걸리는 곳: `app.py:644` — 전체 등락비율 0.45 미만이면 후보 전량 탈락. 2026-09-10 실측 0.237로 후보 12,558건이 전부 컷됐고 추천 0건이었다.
- 적용 시 변화: 아래 검증 결과 참조.
- 자료:
  - [The Seven Sins of Quantitative Investing §8.2](https://portfoliooptimizationbook.com/book/8.2-seven-sins.html) — 필터 추가는 곧 파라미터 추가이며 과최적화 표면을 넓힌다

### [검증 완료 2026-09-10] 0.45에는 근거가 없다

**방법**: `validation_backtest`로 `MIN_MARKET_UP_RATIO`를 0.00~0.60까지 스윕(2022-06-30~2026-08-28, 1018 거래일, 102종목).

**결과 1 — 수익률로는 어떤 임계값도 구분되지 않는다.**

| 임계값 | 거래 | 평균수익% | 95% CI | 0.45 대비 |
|---|---|---|---|---|
| 0.00 | 1630 | 0.578 | [-0.35, 1.51] | 0.68 SE |
| 0.25 | 1449 | 0.416 | [-0.25, 1.08] | 0.49 SE |
| **0.45** | 1030 | 0.178 | [-0.51, 0.87] | — |
| 0.60 | 619 | 0.779 | [-0.27, 1.83] | 0.94 SE |

전부 1.96 SE에 한참 못 미친다. 참고로 초과수익은 모든 임계값에서 음수이며, 0.45에서는 95% CI가 [-1.78, -0.57]로 **0을 포함하지 않는다** — 이 설정에서 전략은 벤치마크에 유의하게 뒤진다.

**결과 2 — MDD 개선은 필터 효과가 아니라 그냥 덜 산 결과다.**

임계값을 올리면 MDD가 -55%→-28%로 좋아진다. 그런데 무필터 거래에서 **같은 수만큼 무작위로 버린** 대조군과 비교하면:

| 임계값 | 거래 | 필터 MDD% | 무작위 축소 MDD 95% CI | 판정 |
|---|---|---|---|---|
| 0.25 | 1449 | -44.00 | [-61.21, -41.57] | 무작위와 동급 |
| **0.45** | 1030 | -44.95 | [-56.89, -27.52] | 무작위와 동급 |
| 0.50 | 886 | -34.52 | [-54.92, -23.93] | 무작위와 동급 |
| 0.60 | 619 | -28.32 | [-45.26, -17.76] | 무작위와 동급 |

**모든 임계값에서 필터 MDD가 무작위 축소의 신뢰구간 안에 들어간다.** 필터는 노출을 줄이는 것 외에 아무 정보도 더하지 않는다.

**결론**: 0.45는 수익률로도 MDD로도 정당화되지 않는다. 특히 0.25 대비 0.45는 차단일을 3배(15%→46%)로 늘리면서 MDD는 1~3%p밖에 못 줄인다.

### [적용됨 2026-09-10] 하드 게이트를 0.25로 낮춰 소프트 사다리를 살렸다

노출 조절 수단이 두 개인데 하나가 다른 하나를 무력화한다.

- `market_exposure_limit_pct`(`app.py:620-622`): 등락비율 ≥0.60이면 100%, ≥0.45면 40%, **그 미만이면 10%**
- `passes_market_filter`(`app.py:643`): 0.45 미만이면 추천 자체를 0건으로

후자가 먼저 걸리므로 사다리의 **10% 구간은 라이브 매수 경로에서 도달 불가능**하다(`app.py:1367`은 픽이 있을 때만 실행). 이미 구현된 점진적 노출 축소가 이진 게이트에 가려 안 쓰이고 있다.

**조치**: `MIN_MARKET_UP_RATIO`를 0.45 → 0.25로 낮췄다(`.env`, `.env.example`, `README.md`, 그리고 `app.py:644`·`health.py:58`·`validation_backtest.py:95,457`의 하드코딩 기본값). 이제 등락비율 0.25~0.45 구간이 매수 경로에 도달하므로 **사다리의 10% 구간이 처음으로 실제 동작한다** — 예전에는 그 구간이 통째로 0건이었다.

### 이 검증의 한계 (그대로 믿지 말 것)

- **측정치가 다르다.** 백테스트는 관심종목 102개 기준 등락비율, 라이브는 KOSPI+KOSDAQ 전체 기준이다. 같은 0.45라도 백테스트 차단율 45.6%, 라이브 실측 차단율 16%(2026-08-06~09-10, 25일 중 4일 전량 차단)로 다르다. **임계값 숫자가 그대로 옮겨가지 않는다.**
- 임계값 5개를 한 과거 경로에서 시험했다. §1의 다중검정 문제가 이 분석 자체에도 적용된다. 다만 "차이가 없다"는 귀무결과라 양성 결과보다는 견고하다.
- 백테스트는 생존편향(현재 watchlist를 과거 전 기간에 적용)이 있고 뉴스·공시 점수를 0으로 둔다.

---

## 3. 팩터 설계

담당 손잡이: 팩터 7종 (`strategy_learning.py:22` `FACTORS`), 프로파일 카테고리 6종 (`trading_profiles.py` `scoring_weights`)
코드 위치: `stock_alarm/app.py:433` (`category_scores`), `:847` (`profile_total_score`), `:852` (`select_for_profile`)

관련 메모: `news_score`·`disclosure_score`는 현재 가중치 0(`NEWS_SCORE_WEIGHT=0`, `DART_SCORE_WEIGHT=0`). 뉴스 키워드 감성은 어느 구간에서도 수익률을 예측하지 못했다.

### 횡단면 정규화
- 개념: 팩터 점수는 절대값이 아니라 **그날 다른 종목 대비** 몇 등인지가 의미를 갖는다. 종목 간 비교 가능하게 만들려면 z-score나 순위 변환을 쓰고, 극단값은 winsorize한다.
- 걸리는 곳: `app.py:433` `category_scores` — 현재는 `_scale`로 절대 구간을 점수로 매핑한다(거래량 급증 최대 40점 등). 시장 전체가 거래량이 터진 날과 조용한 날의 "40점"이 같은 의미가 아니다.
- 적용 시 변화: 같은 날 후보들 사이의 상대순위로 바꾸면 시장 상황에 따른 점수 인플레이션이 사라진다. `_ranked_returns`(`strategy_learning.py:185`)가 어차피 상대순위로 상위 절반을 고르므로 학습과도 더 잘 맞는다.
- 자료: [Scaling / normalisation / standardisation (Quantdare)](https://quantdare.com/scaling-normalisation-standardisation-a-pervasive-question/)

### 팩터 간 상관과 크라우딩
- 개념: 팩터를 더 넣는다고 정보가 비례해 늘지 않는다. 서로 상관된 팩터는 같은 베팅을 중복으로 키우고, 평시엔 낮던 상관이 급락장에서 갑자기 1로 수렴한다.
- 걸리는 곳: `strategy_learning.py:22` — 7개 팩터를 **단순 가중합**한다. `volume_score`와 `trading_value_score`는 정의상 겹치고(둘 다 거래 활발도), `trend_score`와 `momentum_score`도 마찬가지다.
- 적용 시 변화: 팩터 상관행렬을 먼저 보고 중복을 합치거나 직교화한다. `reports/backtest/factor_correlation_matrix.csv`가 이미 있으니 새로 만들 필요는 없다.
- 자료: [Khandani & Lo, "What Happened to the Quants in August 2007?" (JFM 2011) — MIT 전문](https://web.mit.edu/Alo/www/Papers/august07.html) — 서로 낮게 상관됐다고 믿은 팩터들이 동시에 청산되며 실현 상관이 1로 튄 실제 사례. ([NBER 워킹페이퍼 PDF](https://www.nber.org/system/files/working_papers/w14465/w14465.pdf))

### 모멘텀 크래시 — 이 시스템의 알려진 약점
- 개념: 모멘텀 전략은 평시 수익이 좋지만 드물게 큰 연속 손실을 낸다. **하락 후 변동성이 높은 "패닉 국면"에서, 시장이 반등하는 바로 그 시점에** 터진다 — 부분적으로 예측 가능하다.
- 걸리는 곳: `trading_profiles.py`의 aggressive 프로파일이 momentum 0.25로 최대 비중이고, 코드 주석에 이미 "횡보장에서 유독 약함(모멘텀이 자꾸 반전당함)"이 기록돼 있다. 대응책이 `regime_exposure_multiplier: {"sideways": 0.5}`다.
- 적용 시 변화: 논문의 진단(하락 후 + 고변동성)은 현재 쓰는 "횡보장" 레짐 라벨과 다른 축이다. 변동성 조건을 추가하면 같은 약점을 더 정확히 겨냥할 수 있다.
- 자료: [Daniel & Moskowitz, "Momentum Crashes" (JFE 2016) 전문 PDF](https://www.kentdaniel.net/papers/published/jfe_16.pdf) ([NBER 버전](https://www.nber.org/papers/w20439))

---

## 4. 포지션 사이징

담당 손잡이: `VIRTUAL_TRADER_POSITION_SIZING_MODE=fixed`, `VIRTUAL_TRADER_FIXED_POSITION_PCT=10`, `VIRTUAL_TRADER_MIN/MAX_POSITION_PCT=10/30`, `CORRELATED_GROUP_MAX_PCT=40`, `RISK_MAX_EXPOSURE_PCT=70`, `SECTOR_GROUP_MAX_PCT=100`(비활성), 프로파일별 `regime_exposure_multiplier`
코드 위치: `stock_alarm/app.py:1207-1211`·`:1248`, `stock_alarm/portfolio_risk.py:45`, `stock_alarm/trading_profiles.py`

### 고정 비중 vs 변동성 타게팅
- 개념: 모든 종목에 같은 금액을 넣으면 **변동성이 큰 종목이 포트폴리오 위험을 독차지한다.** 위험 기여도를 맞추려면 비중을 변동성에 반비례시킨다.
- 걸리는 곳: `app.py:1207-1208` — `sizing_mode=fixed`, 종목당 총자산 10% 고정. ATR은 이미 계산해서 `atr20_pct`로 들고 있는데(추천 메시지에도 표시된다) 사이징에는 안 쓴다.
- 적용 시 변화: 비중을 `10% × (목표ATR / 종목ATR)`로 바꾸면 변동성 큰 종목의 비중이 자동으로 줄어든다. 이미 있는 값을 쓰는 것이라 새 데이터가 필요 없다.
- 자료: [Risk parity (Wikipedia)](https://en.wikipedia.org/wiki/Risk_parity) / [Position sizing 전략 정리](https://www.quantifiedstrategies.com/position-sizing-strategies/)

### 켈리와 분수 켈리
- 개념: 켈리 공식은 승률과 손익비로 장기 성장률을 최대화하는 베팅 비율을 준다. 다만 **켈리를 넘기면 성장률과 변동성이 동시에 나빠지고**, 2배 켈리에서 기대 성장률이 0이 된다. 실무자는 보통 켈리의 10~25%만 쓴다.
- 걸리는 곳: 10% 고정 비중이 켈리 대비 어디쯤인지 계산된 적이 없다. 백테스트 승률 24.95%, 손익비(payoff) 3.20이 이미 측정돼 있으므로 대입만 하면 나온다.
- 적용 시 변화: 켈리 비율을 계산해보면 현재 10%가 과대·과소 베팅인지 판정할 수 있다. 승률이 25%로 낮으므로 결과가 직관과 다를 수 있다.
- 자료: [Kelly criterion (Wikipedia)](https://en.wikipedia.org/wiki/Kelly_criterion) / [변동성 정보를 결합한 동적 켈리 (arXiv)](https://arxiv.org/html/2508.16598v1)

---

## 5. 매도 규칙

담당 손잡이: `SELL_LOSS_PCT=5`, `SELL_ATR_MULTIPLIER=2`, `SELL_TIME_STOP_DAYS=10`, `SELL_TIME_STOP_MIN_RETURN_PCT=0`
코드 위치: `stock_alarm/sell_check.py:96-97` (고정·ATR 손절), `:135` (시간 손절)

### 손절이 모멘텀 전략에 주는 효과
- 개념: 모멘텀 전략에 손절을 붙이면 크래시 구간의 최악 손실이 크게 줄고 Sharpe가 개선된다는 실증이 있다. 손절의 값어치는 평균 수익이 아니라 **꼬리 위험**에서 나온다.
- 걸리는 곳: `sell_check.py:96-97` — 고정 5%와 ATR 2배 중 적용. 이 값들이 백테스트로 정해졌는지 기록이 없다.
- 적용 시 변화: 손절 폭을 평균 수익률로 평가하면 거의 항상 "손절은 손해"로 나온다. MDD와 최악 구간 손실로 평가해야 한다 — §2에서 레짐 필터를 평가할 때와 같은 함정이다.
- 자료: [Daniel & Moskowitz, "Momentum Crashes"](https://www.kentdaniel.net/papers/published/jfe_16.pdf) — 크래시가 언제 오는지가 곧 손절이 언제 필요한지다

### ATR 배수와 휩소
- 개념: 변동성 기반 손절은 종목마다 다른 변동성에 맞춰 폭을 조절해 휩소를 줄인다. 다만 배수가 너무 작으면 정상 눌림에도 털리고, 횡보장에서는 어떤 배수든 휩소가 늘어난다.
- 걸리는 곳: `SELL_ATR_MULTIPLIER=2`. 일반적인 권장 범위는 스윙 기준 2~4배이고 2는 그 하단이다. 여기에 `SELL_LOSS_PCT=5` 고정 손절이 함께 걸려 **둘 중 먼저 닿는 쪽**이 발동하므로, 변동성이 큰 종목은 사실상 고정 5%가 지배한다.
- 적용 시 변화: 고정 손절과 ATR 손절이 실제로 각각 몇 번 발동했는지부터 세면 어느 쪽이 실질 규칙인지 드러난다. `logs/sell_alerts.csv`에 사유가 기록돼 있다.
- 자료: [ATR 기반 손절 실무 가이드](https://www.luxalgo.com/blog/how-to-use-atr-for-volatility-based-stop-losses/)

### 시간 손절
- 개념: 기대한 기간 안에 움직이지 않은 포지션은 가설이 틀린 것이므로 자본을 회수한다.
- 걸리는 곳: `sell_check.py:135` — 10거래일 보유 후 수익률 0 이하면 매도. 학습 목적함수의 최장 구간(10일)과 우연히 같은 값이다.
- 적용 시 변화: 표본 정의를 20일 성숙으로 바꿨으므로(§1), 10일에 자르는 시간 손절과 20일까지 성과를 측정하는 학습 사이에 구간 불일치가 생겼다. 10일에 팔린 종목의 20일 수익률이 무엇을 의미하는지 정리가 필요하다.
- 자료:
