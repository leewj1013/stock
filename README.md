# stockAlarm

국내주식 추천 후보, 보유종목 매도 검토, 아침 시황 요약, 마감 후 일일요약/이슈 알림을 텔레그램으로 보내는 로컬 자동화 프로젝트입니다.

이 프로젝트는 투자 조언이 아니라 “검토할 만한 후보와 위험 신호를 알려주는 감시 도구”입니다. 최종 매수/매도 판단은 직접 확인해야 합니다.

## 가장 쉬운 사용법

평소에는 아래 배치 파일만 사용하면 됩니다.

```text
start_stock_alarm.bat  - 작업 스케줄러 등록, 상태 점검, 대시보드 생성/열기
open_dashboard.bat     - 대시보드 새로고침 후 열기
issue_alert.bat        - 현재 이슈 알림만 수동 발송
set_dart_key.bat       - OpenDART API 키 등록
```

PC를 재부팅했거나 자동 실행 상태를 다시 맞추고 싶으면 `start_stock_alarm.bat`을 실행하세요.

## 자동 실행 배치

`start_stock_alarm.bat`을 실행하면 Windows 작업 스케줄러에 아래 작업이 등록됩니다.

| 작업 이름 | 실행 시간 | 실행 모드 | 주요 내용 |
|---|---:|---|---|
| `stockAlarmOpen` | 매일 08:30 | `open` | 미국·국내 아침 시황 요약 |
| `stockAlarmIntradayEvery5Minutes` | 평일 08:50~15:40, 5분마다 | `intraday` | 추천 후보 확인, 가상 트레이더 수익률 변화 기록 |
| `stockAlarmSellEvery5Minutes` | 평일 08:50~15:40, 5분마다 | `sell` | 독립 매도 조건 점검, 보유 수익률 갱신 |
| `stockAlarmDaily` | 매일 16:00 | `daily` | 마감 종가 재수집, 추천 성과, 일일요약, 상태점검, 대시보드, 이슈 알림 |

`open`은 개장 전(08:30)에 돌기 때문에 그날 캔들 발행 여부로는 휴장일을 판단할 수 없어, `stock_alarm/run_gate.py`의 `KR_MARKET_HOLIDAYS` 정적 목록으로 평일 휴장일을 걸러냅니다. 이 목록은 매년 갱신이 필요하며, 최신 목록은 한국거래소 공시채널(kind.krx.co.kr)의 연간 휴장일 공지를 참고하세요. `intraday`/`sell`/`daily`는 장중·마감 이후에 돌아서 실제 캔들 발행 여부(`is_trading_day()`)로 판단하므로 이 목록과 무관합니다.

현재 `scripts/run_stock_alarm.ps1` 기준 실행 흐름은 아래와 같습니다.

```text
open
- market_summary
- recommendation

intraday
- recommendation
- virtual_trader_report
- dashboard

sell
- sell_check
- positions_report

daily
- positions_report
- recommendation_performance
- strategy_learning
- daily_summary
- daily_check
- dashboard
- issue_alert

performance
- recommendation_performance

issue_alert
- issue_alert
```

## 자동 학습과 위험관리

- 추천 당시 점수 구성과 이후 1·3·5·10·20거래일 성과를 DB에 누적합니다.
- 유효 표본 300건 이상부터 3개 이상의 Walk-forward 구간(구간당 최소 60건)을 검증합니다.
- 모든 구간의 수익률 개선, MDD 악화 2%p 이내, 비모수 검정 p-value 0.05 미만을 모두 만족한 가중치만 다음 거래일부터 적용합니다.
- 가중치는 하루 최대 5%p, 기본값의 75~125% 범위에서만 변경됩니다.
- 일 -2%, 주 -5%, 계좌 고점 대비 -10%에 도달하면 신규 가상매수만 중단하며 매도 점검은 계속됩니다.
- 가격 날짜·OHLC·최신성 검증에 실패한 종목은 추천, 가상매수와 학습에서 제외됩니다.

참고:

- 추천/매도 알림은 거래일 09:00~15:30 장중에만 발송됩니다.
- 마감 후 일일요약/이슈 알림은 거래일이면 16:00 배치에서 발송됩니다.
- 주말/공휴일처럼 거래일이 아니면 알림성 작업은 스킵됩니다.
- 국내 시황은 네이버 종목 시세로 계산하고, 미국 시황은 Alpha Vantage 키가 있을 때 SPY·QQQ·SOXX·IWM의 최근 마감을 포함합니다.

작업 스케줄러 상태 확인:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\status_daily_task.ps1
```

## 대시보드

대시보드는 아래 파일로 생성됩니다.

```text
reports/dashboard.html
```

새로 만들고 열려면:

```text
open_dashboard.bat
```

### GitHub Pages에서 로컬 DB 실시간 조회

원격 연결은 읽기 전용입니다. 입금·수동매수·브라우저 계좌 이전은 로컬 대시보드에서만 허용됩니다. Cloudflare 터널은 `DASHBOARD_REMOTE_PORT`(기본 8766)만 외부로 연결하며, 전체 대시보드·`/remote-setup`·매수/입금 API가 있는 `DASHBOARD_PORT`(기본 8765)는 터널에 절대 연결되지 않습니다 — 터널을 거치는 요청은 실제 발신지와 무관하게 로컬 접속처럼 보이므로, 두 포트를 분리하는 것 자체가 보안 경계입니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_remote_dashboard.ps1
```

출력된 `https://...trycloudflare.com` 주소와 `.env`의 `DASHBOARD_REMOTE_TOKEN`을 GitHub Pages의 `로컬 DB 실시간 연결` 입력란에 입력합니다. API 주소는 브라우저 로컬 저장소에, 토큰은 현재 탭의 세션 저장소에만 보관됩니다.

주소 입력과 토큰 복사를 도와주는 로컬 설정 화면을 열려면 다음 스크립트를 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\open_remote_dashboard.ps1
```

로컬 설정 화면에서 `토큰 복사`와 `GitHub Pages 열기`를 차례로 누릅니다. GitHub Pages가 열리면 토큰을 붙여넣고 `읽기 전용 연결`을 누릅니다. 토큰은 URL이나 Git 저장소에 포함되지 않습니다.

운영용 고정 주소는 Cloudflare 계정에 활성 도메인을 연결하고 Named Tunnel과 Access 정책을 설정해야 합니다. 임시 주소는 터널을 재시작하면 변경됩니다.

대시보드 주요 내용:

- 상단 요약 카드
  - 총 평균 수익률
  - 1일 평균 수익률
  - 1일 승률
  - 보유 최저 수익률
- 오늘 추천 종목
- 보유 종목
- 최근 매도 검토
- 추천 점수 구성
- 추천 성과
- 추천 통계
- 설정/진단 탭
  - 실행 상태
  - 현재 설정
  - 최근 발송
  - 성과 감점
  - 추천 성과 상위/하위

리스트형 테이블은 한 페이지에 최대 15개 행만 표시하고, 16개 이상이면 페이지 버튼이 표시됩니다.

## 추천 기준

현재 추천 규칙은 단순 룰 기반입니다.

- 거래량이 직전 20거래일 평균 대비 `VOLUME_MULTIPLIER` 이상
- 현재가가 20일 이동평균 위
- 거래대금이 `MIN_TRADING_VALUE` 이상
- 당일 가격 변동폭이 `MAX_DAY_CHANGE_PCT` 이하
- 신규 진입일 상승률이 `MAX_ENTRY_DAY_CHANGE_PCT` 이하
- 20일선 이격률이 절대 상한과 ATR 기반 상한 안쪽
- 최근 평균 장중 변동폭이 `MAX_AVG_RANGE_PCT` 이하
- KOSPI·KOSDAQ 전체 종목 등락비율(한국거래소 공식 Open API 기준, `KRX_API_KEY` 없거나 실패 시 네이버 시가총액 상위 페이지 스크랩으로, 그마저 실패하면 관심종목 기준으로 폴백)이 `MIN_MARKET_UP_RATIO` 이상
- 추천 점수가 `MIN_RECOMMEND_SCORE` 이상
- 이미 추천되어 추적 중인 종목은 매도 알림이 올 때까지 중복 추천 제외
- 매도 알림 이후 다시 추천된 종목은 다시 추적 대상으로 보고 중복 추천 제외
- 상위 `TOP_N`개 후보 발송

기본 점수 구성:

```text
거래량 급증        최대 40점
거래대금           최대 30점
20일선 적정 이격   최대 30점
```

뉴스, 공시, 재무정보, 시장 대비 상대강도, 과거 추천 성과 감점과 학습 가중치를 함께 반영합니다.

시장 대비 상대강도의 기준선은 `MARKET_BENCHMARK_TICKER`를 따로 지정하지 않은 경우 KOSPI 지수 하나가 아니라 KOSPI·KOSDAQ **전체 종목의 평균 등락률**(`KRX_API_KEY` 있으면 공식 Open API, 없으면 네이버 스크랩)을 씁니다. 대형주 위주인 단일 지수보다 시장 전체 분위기를 더 폭넓게 반영하기 위함입니다.

`DYNAMIC_SCREENING_TOP_N`(기본 0/꺼짐)을 0보다 크게 설정하면, 그날 KOSPI·KOSDAQ 전체에서 거래대금 상위 N개 종목(우선주 제외)을 `data/watchlist.csv`에 추가로 합쳐서 평가합니다. 새로 추가된 종목도 위의 모든 필수조건을 동일하게 통과해야 실제 추천으로 이어지며, 관심종목에 이미 있는 이름은 덮어쓰지 않습니다. `KRX_API_KEY`가 없으면 조용히 꺼진 채로 동작합니다.

## 매도 검토 기준

보유/추천 추적 중인 종목은 장중 배치에서 매도 검토를 수행합니다.

대표 기준:

- 손절 기준 이하
- 20일선 이탈
- 직전 수익률 대비 급격한 악화 + 손절 또는 20일선 2회 이탈 확인
- 고점 대비 수익 반납 + 손절 또는 20일선 2회 이탈 확인
- 보유일수 대비 기대수익 미달(시간 손절)
- 수익률 `TAKE_PROFIT_1_PCT` 도달 시 1차 부분 익절, `TAKE_PROFIT_2_PCT` 도달 시 잔량 2차 익절

이미 매도 알림을 보낸 종목은 같은 매도 알림 대상에서 제외됩니다.

매도 후 재추천 냉각 기간은 매도 사유에 따라 다릅니다(손절 `SELL_RECOMMEND_COOLDOWN_STOP_LOSS_DAYS` 기본 5일, 시간 손절 `SELL_RECOMMEND_COOLDOWN_TIME_STOP_DAYS` 기본 3일, 20일선 이탈 `SELL_RECOMMEND_COOLDOWN_MA20_DAYS` 기본 2일, 익절 `SELL_RECOMMEND_COOLDOWN_TAKE_PROFIT_DAYS` 기본 1일). 1차 부분 익절은 포지션이 계속 유지되므로 냉각 기간 없이 추천 이력이 이어집니다.

## 아침 시황 요약

08:30 `open` 배치에서 시황 요약을 보냅니다. 추천 검사는 08:50부터 별도 장중 작업으로 실행됩니다.

시황 요약 내용:

- 관심종목 수
- 상승/하락 종목 수
- 상승 비율
- 평균 등락률
- 거래대금 상위 3개 종목
- 미국 대표 ETF(SPY·QQQ·SOXX·IWM) 최근 마감 등락률
- 공격·중립·방어 시장 판단과 신규 매수 한도

국내 부분은 `data/watchlist.csv`의 관심종목 기준입니다. 미국 부분은 `ALPHA_VANTAGE_API_KEY`가 필요하며, 실패하면 마지막 정상 캐시를 사용합니다.

## 토스증권 Open API 연결

`.env`의 `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`으로 OAuth2 토큰을 발급하고,
삼성전자 현재가와 계좌 목록을 읽기 전용으로 확인합니다. 토큰과 계좌번호는
출력하거나 파일에 저장하지 않습니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.toss_check
```

호출 전 토스증권 WTS의 `설정 > Open API > 허용 IP 관리`에서 이 PC가 사용하는
공인 IP를 등록해야 합니다. 현재 구현에는 주문 API가 없으며
`TOSS_TRADING_ENABLED=0`이 기본값입니다.

## 텔레그램 설정

`.env` 파일에 아래 값이 필요합니다.

```text
NOTIFIER=telegram
TELEGRAM_BOT_TOKEN=텔레그램_봇_토큰
TELEGRAM_CHAT_ID=텔레그램_CHAT_ID
DATA_SOURCE=naver
KOREAN_STOCK_NAMES=1
AUTO_TRACK_PICKS=1
```

텔레그램 테스트:

```powershell
.\.venv\Scripts\python -m stock_alarm.telegram_test
```

봇 토큰 확인:

```powershell
.\.venv\Scripts\python -m stock_alarm.telegram_check
```

발송 기록:

```text
logs/deliveries.csv
logs/sent_keys.csv
```

## OpenDART 설정

OpenDART API 키를 받으면 `set_dart_key.bat`을 실행해서 등록하세요.

등록 후 특정 종목 공시 점검:

```powershell
.\.venv\Scripts\python -m stock_alarm.dart_reference 005930
```

OpenDART 키가 없어도 기본 추천, 텔레그램, 시황 요약, 대시보드는 동작합니다.

## 설치

처음 한 번만 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item data\positions.example.csv data\positions.csv
```

그 다음 `.env`에 텔레그램 값을 입력하세요.

기존 DB를 새 스키마로 올릴 때는 다음 명령을 한 번 실행합니다. 마이그레이션은 여러 번 실행해도 안전하며, 평상시에는 앱 시작 시에도 자동 적용됩니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.migrate_db
```

## 주요 설정값

추천·매도 성과 계산에는 다음 보정값도 사용합니다.

```text
EXECUTION_COST_BPS=30
PERFORMANCE_MIN_SAMPLES=20
FUNDAMENTAL_LOOKUP=1
MARKET_BENCHMARK_TICKER=KOSPI
SELL_ATR_MULTIPLIER=2
SELL_TIME_STOP_DAYS=10
SELL_TIME_STOP_MIN_RETURN_PCT=0
```

- 장중 거래량은 시간대별 예상 누적 거래량으로 보정합니다.
- 추천 성과는 다음 거래일 시가 진입과 왕복 거래비용을 기준으로 계산합니다.
- 매도는 ATR 동적 손절, 20일선 2회 확인, 시간 손절을 함께 사용합니다.
- 가상계좌는 기본 +10%에서 50%를 1차 익절하고 +20%에서 잔량을 2차 익절합니다. 초기값은 백테스트 후 조정해야 합니다.
- 포트폴리오 위험 한도 도달 시 신규매수만 중단하며 보유종목은 유지합니다. 분할익절과 개별 손절·매도 감시는 계속됩니다.
- 매도 이후 1·3·5·10일 반대성과는 `logs/sell_performance.csv`와 SQLite에 저장합니다.

`.env.example` 기준 주요 설정입니다.

```text
NOTIFIER=telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
FORCE_SEND=0
MARKETS=KOSPI,KOSDAQ
DATA_SOURCE=naver
KOREAN_STOCK_NAMES=1
AUTO_TRACK_PICKS=1
TOP_N=5
MIN_TRADING_VALUE=5000000000
VOLUME_MULTIPLIER=1.5
MAX_DAY_CHANGE_PCT=8
MAX_ENTRY_DAY_CHANGE_PCT=5
MAX_MA20_DISTANCE_PCT=10
MAX_MA20_DISTANCE_ATR=1.5
MAX_AVG_RANGE_PCT=12
MIN_MARKET_UP_RATIO=0.45
SELL_LOSS_PCT=5
SELL_DROP_PCT=3
SELL_PROTECT_PROFIT_PCT=5
SELL_GIVEBACK_PCT=4
SELL_RECOMMEND_COOLDOWN_DAYS=3
SELL_RECOMMEND_COOLDOWN_STOP_LOSS_DAYS=5
SELL_RECOMMEND_COOLDOWN_MA20_DAYS=2
SELL_RECOMMEND_COOLDOWN_TIME_STOP_DAYS=3
SELL_RECOMMEND_COOLDOWN_TAKE_PROFIT_DAYS=1
SEND_EMPTY_SELL_ALERT=0
SEND_DAILY_CHECK_ALERT=0
MIN_RECOMMEND_SCORE=50
DART_API_KEY=
DART_LOOKUP=0
DART_SCORE_WEIGHT=1
FUNDAMENTAL_LOOKUP=1
ALPHA_VANTAGE_API_KEY=
VIRTUAL_TRADER_AUTO_BUY=1
VIRTUAL_TRADER_MIN_POSITION_PCT=10
VIRTUAL_TRADER_MAX_POSITION_PCT=30
TAKE_PROFIT_1_PCT=10
TAKE_PROFIT_1_SELL_RATIO=50
TAKE_PROFIT_2_PCT=20
LEARNING_MIN_SAMPLES=300
LEARNING_MAX_SAMPLES=1000
LEARNING_VALIDATION_MIN_SAMPLES=60
LEARNING_VALIDATION_FOLDS=3
LEARNING_SIGNIFICANCE_LEVEL=0.05
```

## 주요 로그 파일

```text
logs/recommendations.csv                    추천 후보 기록
logs/sell_alerts.csv                        매도 검토 알림 기록
logs/positions_report.csv                   보유 종목 수익률 기록
logs/recommendation_performance.csv         추천 성과 상세
logs/recommendation_performance_summary.csv 추천 성과 요약
logs/deliveries.csv                         알림 발송 기록
logs/sent_keys.csv                          중복 발송 방지 키
logs/task.out.log                           자동 실행 출력 로그
logs/task.err.log                           자동 실행 오류 로그
logs/errors.log                             앱 오류 로그
```

### 분석용 원천 데이터

추천 및 매도 로직을 다시 실험할 수 있도록 `data/stock_alarm.db`에도 원천 판단 데이터를 저장합니다.

- `strategy_runs`: 실행 ID, 시장일, 전략/스키마 버전, 당시 설정값
- `candidate_snapshots`: 관심종목 전체의 가격·거래량·이동평균·점수·탈락 사유·선정 여부
- `position_checks`: 보유종목 전체의 수익률·고점 대비 하락·조건별 발동 여부·`HOLD/SELL` 판단

CSV는 기존 화면과 보고서 호환을 위해 계속 생성됩니다. 추천 성과에는 1·3·5·10·20 거래일 수익률과 20거래일 최대 유리 변동폭(MFE), 최대 불리 변동폭(MAE)이 포함됩니다.

## 수동 실행

일반 운영은 배치 파일로 충분합니다. 아래 명령은 문제 확인이나 개발할 때만 사용하세요.

상태 점검:

```powershell
.\.venv\Scripts\python -m stock_alarm.health
```

아침 시황 요약 수동 발송:

```powershell
.\.venv\Scripts\python -m stock_alarm.market_summary
```

추천 후보 수동 실행:

```powershell
.\.venv\Scripts\python -m stock_alarm
```

매도 검토 수동 실행:

```powershell
.\.venv\Scripts\python -m stock_alarm.sell_check
```

보유 수익률 리포트:

```powershell
.\.venv\Scripts\python -m stock_alarm.positions_report
```

추천 성과 계산:

```powershell
.\.venv\Scripts\python -m stock_alarm.recommendation_performance
```

일일 요약 수동 발송:

```powershell
.\.venv\Scripts\python -m stock_alarm.daily_summary
```

이슈 알림 수동 발송:

```powershell
.\.venv\Scripts\python -m stock_alarm.issue_alert
```

대시보드 수동 생성:

```powershell
.\.venv\Scripts\python -m stock_alarm.dashboard
```

대시보드의 `추천 추적` 탭은 가상계좌 매수 여부와 무관하게 모든 추천 신호를 보존하며,
추천가·현재 성과·매도 알림일·매도 사유·가상매수 여부를 한 화면에서 확인합니다.
`가상 트레이더` 탭은 실제 가상계좌 주문과 보유·매도 내역만 별도로 표시합니다.

## 백테스트와 튜닝

최근 거래일 기준으로 추천 규칙을 테스트합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.backtest
```

거래량 배수와 보유 기간 조합을 비교합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.tune
```

튜닝 결과 요약:

```powershell
.\.venv\Scripts\python -m stock_alarm.tune_report
```

결과 파일:

```text
logs/backtest.csv
logs/backtest_summary.csv
logs/tuning.csv
```

### 3년 이상 전략 검증 파이프라인

강화된 Walk-forward 승격 조건과 분할익절 전후 성과를 검증할 때 사용합니다. 이 경로는 `data/stock_alarm.db`와 가상계좌를 수정하지 않으며, 전용 파일 경로만 사용합니다.

```powershell
# 1. 2022년부터 현재까지 관심종목·KOSPI 일봉 수집 및 품질검사
.\.venv\Scripts\python -m stock_alarm.backtest_data

# 2. 추천·매도·분할익절·Walk-forward 검증 실행
.\.venv\Scripts\python -m stock_alarm.validation_backtest

# 3. 필수조건 통과 전체 사례의 7개 요인 예측력 분석
.\.venv\Scripts\python -m stock_alarm.factor_analysis

# 4. 요인분석 기반 가중치 변형안 포트폴리오 비교
.\.venv\Scripts\python -m stock_alarm.weight_variant_backtest

# 5. 요인·가중치 결과의 Newey-West 및 BH-FDR 재검증
.\.venv\Scripts\python -m stock_alarm.robustness_analysis

# 6. 시총 상위 300개·2018년 이후 확장 표본 수집 및 power 재검증
.\.venv\Scripts\python -m stock_alarm.expanded_factor_analysis

# 이미 수집한 확장 OHLCV만 재사용
.\.venv\Scripts\python -m stock_alarm.expanded_factor_analysis --reuse-data
```

입력 데이터와 결과 위치:

```text
data/backtest/ohlcv/                         격리된 종목별 OHLCV
reports/backtest/data_manifest.json          수집 범위와 표본 현황
reports/backtest/data_quality.csv            제외 행·누락 거래일과 사유
reports/backtest/regime_labels.csv            상승·하락·횡보 국면 라벨
reports/backtest/trades_without_partial_profit.csv
reports/backtest/trades_with_partial_profit.csv
reports/backtest/performance_summary.csv      국면별·전체 성과
reports/backtest/walk_forward_folds.csv       구간별 성과와 p-value
reports/backtest/parameters.json              재현용 실행 설정
reports/backtest/REPORT.md                    최종 요약 리포트
reports/backtest/factor_samples.csv           시점별 원자 요인과 1·3·5·10·20일 수익률
reports/backtest/factor_correlations.csv      전체·국면별 일별 IC와 p-value
reports/backtest/factor_quantiles.csv         일별 횡단면 분위 수익률
reports/backtest/factor_correlation_matrix.csv 요인 간 스피어만 상관행렬
reports/backtest/factor_verdicts.csv           가중치와 예측력 비교·검토 제안
reports/backtest/factor_parameters.json        요인 분석 재현 설정
reports/backtest/FACTOR_REPORT.md              요인 분석 최종 리포트
reports/backtest/weight_variant_trades.csv     변형안별 분할익절 포함 거래
reports/backtest/weight_variant_performance.csv 전체·국면별 성과
reports/backtest/weight_variant_comparison.csv baseline 대비 차이와 p-value
reports/backtest/weight_variant_parameters.json 재현 설정과 실험 config 스냅샷
reports/backtest/WEIGHT_VARIANT_REPORT.md       가중치 비교 최종 리포트
reports/backtest/factor_hac_fdr.csv             160개 요인 검정의 HAC·FDR 결과
reports/backtest/variant_hac_fdr.csv            12개 variant 비교의 HAC·FDR 결과
reports/backtest/factor_verdict_corrections.csv 보정 전후 요인 판정
reports/backtest/robustness_parameters.json     HAC lag·FDR 범위 재현 설정
reports/backtest/STATISTICAL_ROBUSTNESS_REPORT.md 통계 강건성 최종 부록
reports/backtest/EXPANDED_SAMPLE_REPORT.md        확장 전후·power 최종 리포트
reports/backtest/expanded/factor_hac_fdr.csv     확장 표본 160개 HAC·FDR 결과
reports/backtest/expanded/before_after_factor_comparison.csv
reports/backtest/expanded/power_analysis.csv     목표 검정력과 필요 IC 거래일
reports/backtest/expanded/data_quality.csv       확장 데이터 제외 사유
```

국면은 KOSPI 종가의 120일 이동평균과 60일 수익률로 결정합니다. 현재 watchlist를 과거 전체 기간에 적용하므로 생존편향이 있고, 과거 시점 뉴스·공시·재무 스냅샷은 미래정보 누출을 막기 위해 점수에서 제외합니다. p-value 통과도 과최적화 방지를 보장하지 않으므로 실계좌 전환 전 완전 미사용 기간 검증이 추가로 필요합니다.

요인 분석은 같은 날짜의 여러 종목을 독립 표본으로 과대평가하지 않도록 일별 횡단면 스피어만 IC를 주 판정값으로 사용합니다. 뉴스·공시·재무는 과거 공개시점 스냅샷이 현재 데이터에 없으므로 현재 API 값을 소급 적용하지 않으며, 리포트에서 `검증 불가(시점 데이터 없음)`로 구분합니다. 분석은 격리 OHLCV를 읽고 `reports/backtest`에만 쓰며 라이브 DB, 가상계좌, 실제 전략 가중치를 변경하지 않습니다.

가중치 변형안은 `config/backtest_weight_variants.json`에서만 관리합니다. `variant_2`는 추세를 제거하고 30점을 거래량·거래대금에 40:30으로 재분배하며, `variant_3`는 기술 점수를 거래량 70·거래대금 20·추세 10·상대강도 ±2.5로 축소·집중합니다. `variant_4`는 요인분석의 3·5일 평균 IC 절대값 비율로 기술 점수 용량 105점을 배분하는 참고안입니다. 모든 안에서 뉴스·공시·재무 배수는 baseline과 동일하며, 실행해도 운영 가중치는 변경되지 않습니다.

통계 강건성 부록은 겹치는 h일 선행수익률에 `lag=h-1` Newey-West 표준오차를 적용합니다. 요인 검정은 원수익률·초과수익률 전체 160개와 핵심 초과수익률 80개에 Benjamini-Hochberg FDR을 기록하고, variant는 baseline 대비 3개 안 × 4국면의 12개 비교를 보정합니다. 이 명령도 기존 CSV만 읽고 `reports/backtest`에만 쓰며 라이브 DB와 운영 가중치를 변경하지 않습니다.

확장 표본 실험은 네이버 시가총액 표의 KOSPI/KOSDAQ 후보를 합쳐 상위 300개를 선택하고 액면가 0인 ETF·ETN을 제외합니다. 2018년 이후 일봉에 기존 품질검사와 필수 매수조건을 그대로 적용하며, 목록은 `data/backtest_expanded/experimental_watchlist.csv`에만 저장됩니다. 현재 시점 구성종목을 과거에 적용하는 생존편향과 미래 시총 선택 편향이 있으므로 운영 성과가 아니라 검정력 진단으로만 해석해야 합니다. 운영 `data/watchlist.csv`, DB, 가상계좌는 수정하지 않습니다.

### 단순 기준전략 비교

현재 stockAlarm 전체 진입·매도 로직이 KOSPI 매수후보유, watchlist 동일가중
매수후보유, 랜덤 종목선택, 단순 거래량 모멘텀보다 나은지를 같은 비용과
평가기간으로 비교합니다. 랜덤 전략은 기본 100회이며 시드를 기록합니다.
회전매매 전략은 라이브와 같은 `evaluate_risk_state` 위험중단 판정과
`correlation_limited_allocations` 고상관 연결그룹 40% 신규진입 제한을 사용합니다.
섹터 한도는 네이버 업종별 시세에서 생성한 `.cache/sector_mapping.json`을 사용하며,
고상관 제한 뒤에 적용되어 두 제약 중 더 작은 주문비중이 최종값이 됩니다. 기존
보유종목은 강제매도하지 않고 섹터 여력만 소비합니다. `SECTOR_GROUP_MAX_PCT=100`이
기본값이어서 운영에서는 비활성이고, 명시적으로 100 미만을 설정할 때만 작동합니다.

```powershell
# 현재 watchlist 업종 캐시 갱신
.\.venv\Scripts\python -m stock_alarm.sector_reference --refresh

# 섹터 40%/30%/20% 격리 비교 + Newey-West/BH-FDR
.\.venv\Scripts\python -m stock_alarm.sector_limit_backtest
```

섹터 실험 설정은 `config/sector_limit_variants.json`, 결과는
`reports/backtest/SECTOR_LIMIT_REPORT.md`와 `reports/backtest/sector_limit/`에
저장됩니다. 현재 시점 업종 분류를 과거에 고정 적용하므로 업종 변경을 복원하지
못하는 point-in-time 분류 편향(생존편향과 유사)이 있으며 운영 설정은 자동 변경하지
않습니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.benchmark_comparison
```

시장 상승비율별 매수한도(공격 70%/중립 40%/방어 10%) 연결 전후와 MA20 휩쏘·손절 지연 variant를 격리 검증하려면 다음을 실행합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.market_filter_whipsaw_analysis
```

기존 결과는 `reports/backtest/market_filter_whipsaw/before/`에 보존됩니다. 결과 리포트는 `reports/backtest/MARKET_FILTER_WHIPSAW_REPORT.md`, 상세 CSV는 `reports/backtest/market_filter_whipsaw/`에 생성되며 운영 설정·DB는 변경하지 않습니다.

시장필터 연결 후에도 기존 `drawdown_limit` 894거래일 잠금이 재현되는지만 현재 라이브 위험평가기를 그대로 사용해 재검증하려면 다음을 실행합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.market_filter_risk_revalidation
```

리포트는 `reports/backtest/MARKET_FILTER_RISK_REVALIDATION_REPORT.md`, 일별 위험상태와 트리거 이벤트는 `reports/backtest/market_filter_risk_revalidation/`에 저장됩니다. 이 명령은 운영 설정·DB·가상계좌를 변경하지 않습니다.

시장필터 연결 후 포지션 목표비중과 고상관 그룹 제한의 순수 효과를 분리 진단하려면 다음을 실행합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.position_constraint_diagnosis
```

시장 70%/40%/10% 한도와 매도·위험 로직은 고정한 채 목표비중 10%/30%/50%/무상한 및 상관그룹 40%/60%/해제 시나리오만 비교합니다. 리포트는 `reports/backtest/POSITION_CONSTRAINT_DIAGNOSIS_REPORT.md`에 저장되며 운영 설정은 변경하지 않습니다.

확정 baseline(시장필터 연결, 종목당 고정 10%, 고상관 그룹 40%)으로 5개 전략과 랜덤 100회, Newey-West 및 BH-FDR을 정식 재검증하려면 다음을 실행합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.benchmark_baseline_revalidation
```

이미 생성된 정식 벤치마크 CSV로 전후 비교 리포트만 다시 만들 때는 `--reuse-results`를 사용합니다. 최종 리포트는 `reports/backtest/BENCHMARK_BASELINE_REVALIDATION_REPORT.md`에 저장됩니다.

결과는 `reports/backtest/BENCHMARK_COMPARISON_REPORT.md`에 저장되고, 원자료는
`reports/backtest/benchmark_comparison/`에 저장됩니다. 분석은 격리 OHLCV만
읽으며 라이브 DB·가상계좌·실주문 API를 사용하지 않습니다. 현재 watchlist를
과거에 적용하는 생존편향과 뉴스·공시·재무 point-in-time 자료 부재를 감안해
해석해야 합니다.

### KOSPI 대비 수익률 격차 원인 분해

직전 기준전략 비교에서 생성한 동일 조건을 재현해 현금 대기, 매도 후 반등,
거래비용, 종목선정, 포지션 비중 효과를 분리 진단합니다. 일별 현금·보유비중과
부분/전량 매도 이벤트 원장을 별도로 만들며 운영 로직은 변경하지 않습니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.gap_decomposition
```

최종 리포트는 `reports/backtest/GAP_DECOMPOSITION_REPORT.md`, 정확 합산
워터폴은 `reports/backtest/GAP_DECOMPOSITION_WATERFALL.png`, 상세 CSV는
`reports/backtest/gap_decomposition/`에 저장됩니다. 비중 무상한 및 매도 후
추적 결과는 반사실적 진단이며 실제 실행 가능성이나 리스크 관리 성공을 보장하지
않습니다.

### 위험중단·고상관 제한 연결 전후 재검증

기존 미연결 결과를 최초 1회 보존하고, 공통 라이브 제약을 적용한 벤치마크와
격차분해를 모두 재실행한 뒤 전후 비교표와 방어모드 연속 구간을 생성합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.constraint_revalidation
```

최종 리포트는 `reports/backtest/CONSTRAINT_REVALIDATION_REPORT.md`, Claude에
그대로 복사할 전체 텍스트는 `reports/backtest/CONSTRAINT_REVALIDATION_CLAUDE.txt`,
전후 CSV와 보존된 기존 결과는 `reports/backtest/constraint_revalidation/`에
저장됩니다. 라이브 DB·가상계좌·운영 config·실주문 API는 수정하지 않습니다.

### 위험중단 해제정책 대안 백테스트

장기 낙폭 중단이 현금 잠금으로 이어지는 문제를 반등형, 시간 쿨다운형,
낙폭 히스테리시스형, 축소 슬롯 재진입형과 비교합니다. 실험값은
`config/risk_release_variants.json`에만 있으며 운영 환경변수에는 적용되지 않습니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.risk_release_backtest
```

최종 리포트는 `reports/backtest/RISK_RELEASE_VARIANT_REPORT.md`, 전체·국면별
성과와 HAC/FDR 결과, 트레이드오프 산점도 데이터, 일별 상태 및 전환 로그는
`reports/backtest/risk_release_variants/`에 저장됩니다. 실행은 격리 OHLCV만
읽고 운영 config·라이브 DB·가상계좌·실주문 API를 수정하지 않습니다.

### 감쇠형 최고수위 및 합성 낙폭 검증

낙폭 중단 중 월별·분기별·지속기간별로 유효 최고수위를 낮추는 정책을 기존
A/C/E 정책과 비교합니다. KOSPI 일수익률의 20거래일 블록 부트스트랩으로
기본 300개 합성 낙폭 경로도 생성해 탈출률·수익·하락방어를 paired 검정합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.peak_decay_backtest
```

실험 설정은 `config/peak_decay_variants.json`, 최종 리포트는
`reports/backtest/PEAK_DECAY_REPORT.md`, 트레이드오프 그림은
`reports/backtest/PEAK_DECAY_TRADEOFF.png`, 실제·합성 상세 CSV는
`reports/backtest/peak_decay/`에 저장됩니다. 합성 경로는 실제 종목 간 상관과
꼬리위험을 완전히 재현하지 못하며 운영 설정은 자동 변경되지 않습니다.

### 2022년 낙폭 원인 진단

기존 벤치마크의 일별 계좌 상태·거래 이벤트·요인 표본만 읽어 최고점부터
실제 -10% 낙폭 발동일까지 손실 종목, 매도사유, 진입점수, 시장국면 및
시장 breadth를 분해합니다. 백테스트를 다시 실행하지 않습니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.drawdown_cause_analysis
```

최종 리포트는 `reports/backtest/DRAWDOWN_CAUSE_ANALYSIS_REPORT.md`, 상세 CSV는
`reports/backtest/drawdown_cause/`, 계좌·KOSPI 타임라인과 손실기여 차트는
`reports/backtest/DRAWDOWN_CAUSE_TIMELINE.png`에 저장됩니다. 운영 설정·DB·
가상계좌·실주문 API는 수정하지 않습니다.

### Point-in-Time 뉴스·공시·재무 요인 검증

외부요인 원본은 라이브 DB와 분리된 `data/backtest/point_in_time.sqlite3`에
공개시각과 백테스트 가용시각을 함께 저장합니다. 같은 명령을 다시 실행하면
기본키 기준으로 캐시를 갱신하므로 중복 적재하지 않습니다.

```powershell
# 출처별 수집(실패 출처만 따로 재시도 가능)
.\.venv\Scripts\python -m stock_alarm.point_in_time_collect --start 2022-06-30 --sources news,disclosure,financial

# 기존 저장소를 이용한 7요인 IC + Newey-West + BH-FDR 검증
.\.venv\Scripts\python -m stock_alarm.pit_factor_validation --start 2022-06-30

# 수집 후 검증을 한 번에 실행
.\.venv\Scripts\python -m stock_alarm.pit_factor_validation --start 2022-06-30 --collect
```

최종 리포트는 `reports/backtest/POINT_IN_TIME_FACTOR_REPORT.md`, 상세 IC·분위수·
HAC/FDR CSV는 `reports/backtest/`, 종목별 커버리지와 실패 내역은
`reports/backtest/point_in_time/`에 저장됩니다. 네이버 검색 API는 날짜 범위 검색을
지원하지 않고 최신 1,000건까지만 조회되므로 오래된 뉴스는 미수집일 수 있습니다.
OpenDART 목록의 날짜 전용 공시는 다음 영업일부터, PyKRX 일별 재무 스냅샷도
수정 이력 불확실성을 고려해 다음 영업일부터 사용합니다. 낮은 커버리지의 비유의
결과는 신호 부재로 단정하지 않습니다. 이 경로는 운영 설정·DB·가상계좌·실주문
API를 수정하지 않습니다.

## 전략 운영 메모

적용 완료 항목과 데이터 축적 후 검토할 후보는 아래 문서에 정리합니다.

```text
STRATEGY_NOTES.md
```

## 로그 정리

미리보기:

```powershell
.\.venv\Scripts\python -m stock_alarm.cleanup_logs
```

보관 후 현재 로그 비우기:

```powershell
.\.venv\Scripts\python -m stock_alarm.cleanup_logs --apply
```

## 커밋 전 확인

공개 파일에 토큰이나 API 키가 들어갔는지 확인합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.preflight
```
