# stockAlarm

국내주식 추천 후보, 보유종목 매도 검토, 아침 시황 요약, 마감 후 일일요약/이슈 알림을 텔레그램으로 보내는 로컬 자동화 프로젝트입니다.

이 프로젝트는 투자 조언이 아니라 “검토할 만한 후보와 위험 신호를 알려주는 감시 도구”입니다. 최종 매수/매도 판단은 직접 확인해야 합니다.

## 가장 쉬운 사용법

평소에는 아래 배치 파일만 사용하면 됩니다.

```text
start_stock_alarm.bat  - 작업 스케줄러 등록, 상태 점검, 대시보드 생성/열기
open_dashboard.bat     - 대시보드 새로고침 후 열기
open_check.bat         - 08:30 아침 실행 결과 확인
issue_alert.bat        - 현재 이슈 알림만 수동 발송
set_dart_key.bat       - OpenDART API 키 등록
```

PC를 재부팅했거나 자동 실행 상태를 다시 맞추고 싶으면 `start_stock_alarm.bat`을 실행하세요.

## 자동 실행 배치

`start_stock_alarm.bat`을 실행하면 Windows 작업 스케줄러에 아래 작업이 등록됩니다.

| 작업 이름 | 실행 시간 | 실행 모드 | 주요 내용 |
|---|---:|---|---|
| `stockAlarmOpen` | 매일 08:30 | `open` | 아침 시황 요약, 추천 후보 알림 |
| `stockAlarmIntradayEvery5Minutes` | 평일 08:50~15:40, 5분마다 | `intraday` | 추천 후보 확인, 가상 트레이더 수익률 변화 기록 |
| `stockAlarmSellEvery5Minutes` | 평일 08:50~15:40, 5분마다 | `sell` | 독립 매도 조건 점검, 보유 수익률 갱신 |
| `stockAlarmDaily` | 매일 16:00 | `daily` | 마감 종가 재수집, 추천 성과, 일일요약, 상태점검, 대시보드, 이슈 알림 |

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
- 성숙 표본 100건 이상부터 워크포워드 검증을 통과한 점수 가중치를 다음 거래일부터 자동 적용합니다.
- 가중치는 하루 최대 5%p, 기본값의 75~125% 범위에서만 변경됩니다.
- 일 -2%, 주 -5%, 계좌 고점 대비 -10%에 도달하면 신규 가상매수만 중단하며 매도 점검은 계속됩니다.
- 가격 날짜·OHLC·최신성 검증에 실패한 종목은 추천, 가상매수와 학습에서 제외됩니다.

참고:

- 추천/매도 알림은 거래일 09:00~15:30 장중에만 발송됩니다.
- 마감 후 일일요약/이슈 알림은 거래일이면 16:00 배치에서 발송됩니다.
- 주말/공휴일처럼 거래일이 아니면 알림성 작업은 스킵됩니다.
- 시황 요약은 별도 API 키 없이 기존 네이버 종목 시세 데이터로 계산합니다.

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
- 관심종목 상승 비율이 `MIN_MARKET_UP_RATIO` 이상
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

뉴스, 공시, 과거 추천 성과 감점은 확장 가능한 구조로 준비되어 있습니다.

## 매도 검토 기준

보유/추천 추적 중인 종목은 장중 배치에서 매도 검토를 수행합니다.

대표 기준:

- 손절 기준 이하
- 20일선 이탈
- 직전 수익률 대비 급격한 악화 + 손절 또는 20일선 2회 이탈 확인
- 고점 대비 수익 반납 + 손절 또는 20일선 2회 이탈 확인

이미 매도 알림을 보낸 종목은 같은 매도 알림 대상에서 제외됩니다.

## 아침 시황 요약

08:30 `open` 배치에서 추천 알림보다 먼저 시황 요약을 보냅니다.

시황 요약 내용:

- 관심종목 수
- 상승/하락 종목 수
- 상승 비율
- 평균 등락률
- 거래대금 상위 3개 종목

별도 시황 API 키는 필요하지 않습니다. 현재는 `data/watchlist.csv` 또는 기본 감시 종목을 네이버 종목 시세로 조회해 “관심종목 기준 시황”으로 계산합니다.

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
SEND_EMPTY_SELL_ALERT=0
SEND_DAILY_CHECK_ALERT=0
MIN_RECOMMEND_SCORE=50
DART_API_KEY=
DART_LOOKUP=0
```

## 주요 로그 파일

```text
logs/recommendations.csv                    추천 후보 기록
logs/sell_alerts.csv                        매도 검토 알림 기록
logs/positions_report.csv                   보유 종목 수익률 기록
logs/recommendation_performance.csv         추천 성과 상세
logs/recommendation_performance_summary.csv 추천 성과 요약

### 분석용 원천 데이터

추천 및 매도 로직을 다시 실험할 수 있도록 `data/stock_alarm.db`에도 원천 판단 데이터를 저장합니다.

- `strategy_runs`: 실행 ID, 시장일, 전략/스키마 버전, 당시 설정값
- `candidate_snapshots`: 관심종목 전체의 가격·거래량·이동평균·점수·탈락 사유·선정 여부
- `position_checks`: 보유종목 전체의 수익률·고점 대비 하락·조건별 발동 여부·`HOLD/SELL` 판단

CSV는 기존 화면과 보고서 호환을 위해 계속 생성됩니다. 추천 성과에는 1·3·5·10·20 거래일 수익률과 20거래일 최대 유리 변동폭(MFE), 최대 불리 변동폭(MAE)이 포함됩니다.
logs/deliveries.csv                         알림 발송 기록
logs/sent_keys.csv                          중복 발송 방지 키
logs/task.out.log                           자동 실행 출력 로그
logs/task.err.log                           자동 실행 오류 로그
logs/errors.log                             앱 오류 로그
```

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

## 전략 개선 메모

추후 적용 후보는 아래 문서에 정리합니다.

```text
STRATEGY_NOTES.md
```

현재 메모 주제:

- 매도 알림 직후 재추천 냉각 기간
- 아침 시황을 활용한 추천/매도 예외 규칙
- 시장 약세/강세에 따른 점수 기준 조정

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
