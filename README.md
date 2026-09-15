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
| `stockAlarmMaintenance` | 매주 일요일 18:00 | - | DB 무결성 검사, 오래된 스냅샷 정리, 프로필별(적극투자형/위험중립형) 백업 |
| `stockAlarmDashboardServer` | 로그온 시 | - | 로컬 대시보드 API 서버(`stock_alarm.dashboard_server`) 기동 |

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
- **20거래일 성과까지 확정된** 유효 표본 300건 이상부터 3개 이상의 Walk-forward 구간(구간당 최소 60건)을 검증합니다. 1·3·5·10일 성과만 채워진 추천은 아직 표본으로 세지 않습니다.
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

`open_dashboard.bat`을 실행하면 로그인 없이 바로 접속합니다. 전체 대시보드와
가상계좌 쓰기 API는 `127.0.0.1`에만 바인딩되며, 입금·매수 요청은 동일 출처
`Origin` 검사를 통과해야 합니다. 외부 원격 연결은 별도의 읽기 전용 포트와 토큰
보호를 계속 사용합니다.

### GitHub Pages에서 로컬 DB 실시간 조회

원격 연결은 읽기 전용입니다. 입금·수동매수·브라우저 계좌 이전은 로컬 대시보드에서만 허용됩니다. Cloudflare 터널은 `DASHBOARD_REMOTE_PORT`(기본 8766)만 외부로 연결하며, 전체 대시보드·`/remote-setup`·매수/입금 API가 있는 `DASHBOARD_PORT`(기본 8765)는 터널에 절대 연결되지 않습니다 — 터널을 거치는 요청은 실제 발신지와 무관하게 로컬 접속처럼 보이므로, 두 포트를 분리하는 것 자체가 보안 경계입니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_remote_dashboard.ps1
```

출력된 `https://...trycloudflare.com` 주소와 보호 저장소의 `DASHBOARD_REMOTE_TOKEN`을 GitHub Pages의 `로컬 DB 실시간 연결` 입력란에 입력합니다. API 주소는 브라우저 로컬 저장소에, 토큰은 현재 탭의 세션 저장소에만 보관됩니다. 토큰은 입력한 API의 정확한 HTTPS origin에 묶이며 주소가 바뀌면 즉시 삭제되고, 리다이렉트에는 전달되지 않습니다.

주소 입력과 토큰 복사를 도와주는 로컬 설정 화면을 열려면 다음 스크립트를 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\open_remote_dashboard.ps1
```

로컬 설정 화면에서 `토큰 복사`와 `GitHub Pages 열기`를 차례로 누릅니다. GitHub Pages가 열리면 토큰을 붙여넣고 `읽기 전용 연결`을 누릅니다. 토큰은 URL이나 Git 저장소에 포함되지 않습니다.

운영용 고정 주소는 Cloudflare 계정에 활성 도메인을 연결하고 Named Tunnel과 Access 정책을 설정해야 합니다. 임시 주소는 터널을 재시작하면 변경됩니다.

대시보드는 4개 탭으로 구성됩니다.

- **홈**: 오늘의 투자 현황, 가상계좌 성향 비교(적극투자형/위험중립형 총자산·수익률·위험관리 상태를 나란히 표시), 현재 운영 상태, 오늘 추천 종목, 오늘 매도 검토 종목(성향별 컬럼 포함)
- **추천 추적**: 가상매수 여부와 무관하게 모든 추천 신호의 진입가·현재가·매도 알림·매도 사유·가상매수 여부를 추적
- **가상 트레이더**: 상단 토글로 적극투자형/위험중립형 계좌를 전환하며 각 계좌의 잔고·보유종목·위험관리 상태·매도 내역·입금/수동주문을 확인
- **시스템 관리**: 데이터 학습 준비 현황, 이슈 목록, 실행 상태, 현재 설정, 알고리즘 검증 결과(전략별 성과·매도 사유별 결과)

리스트형 테이블은 한 페이지에 최대 15개 행만 표시하고, 16개 이상이면 페이지 버튼이 표시됩니다.

### 위험중립형 가상계좌

적극투자형(실계좌 기준, 텔레그램 알림 대상)과 별도로, 같은 추천 종목을 더 낮은
섹터 한도·노출 한도·타이트한 손절/익절 기준으로 매수하는 위험중립형 가상계좌를
비교용으로 함께 운영합니다. 설정은 `stock_alarm/trading_profiles.py`에서
관리하며, 위험중립형은 텔레그램 알림을 보내지 않고 대시보드에서만 확인합니다.
DB는 `data/stock_alarm_neutral.db`로 완전히 분리되어 있습니다.

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
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_CHAT_ID=<telegram-chat-id>
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
.\.venv\Scripts\python -m pip install -r requirements.lock
Copy-Item .env.example .env
Copy-Item data\positions.example.csv data\positions.csv
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_secrets.ps1
```

`migrate_secrets.ps1`은 `.env`의 API 키·토큰을
`%USERPROFILE%\.stockAlarmSecure\secrets.env`로 옮기고 현재 Windows 사용자·SYSTEM·관리자만
읽을 수 있도록 ACL을 제한합니다. 이후 비밀값은 그 파일에서 관리하고 `.env`에는
비민감 설정만 둡니다. 의존성은 정확한 버전의 `requirements.lock`으로 설치하며,
GitHub Actions도 커밋 SHA로 고정된 액션과 잠금 파일을 사용합니다.

예약작업을 등록할 때는 `register_daily_task.ps1`이 소스·스크립트·가상환경을
`%USERPROFILE%\.stockAlarmSecure\runtime-*`에 복사해 같은 ACL로 잠근 뒤, 모든 예약작업을
그 보호 런타임으로 연결합니다. 보호된 실행기는 Python `-I` 격리 모드로 패키지를
불러오되 DB·로그·보고서는 기존 프로젝트 폴더를 계속 사용합니다. 코드 변경 후에는
예약작업이 검토된 새 코드를 사용하도록 등록 스크립트를 다시 실행해야 합니다.
기존 버전 런타임은 자동 삭제하지 않아 문제가 생기면 작업 경로를 되돌릴 수 있습니다.

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
MIN_MARKET_UP_RATIO=0.25
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

### 전략 검증 파이프라인

강화된 Walk-forward 승격 조건과 분할익절 전후 성과를 검증할 때 사용합니다. 이 경로는 `data/stock_alarm.db`와 가상계좌를 수정하지 않으며, 전용 파일 경로만 사용합니다.

```powershell
# 1. 2022년부터 현재까지 관심종목·KOSPI 일봉 수집 및 품질검사
.\.venv\Scripts\python -m stock_alarm.backtest_data

# 2. 추천·매도·분할익절·Walk-forward 검증 실행
.\.venv\Scripts\python -m stock_alarm.validation_backtest
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
```

국면은 KOSPI 종가의 120일 이동평균과 60일 수익률로 결정합니다. 현재 watchlist를 과거 전체 기간에 적용하므로 생존편향이 있고, 과거 시점 뉴스·공시·재무 스냅샷은 미래정보 누출을 막기 위해 점수에서 제외합니다. p-value 통과도 과최적화 방지를 보장하지 않으므로 실계좌 전환 전 완전 미사용 기간 검증이 추가로 필요합니다.

### 기준전략 비교

현재 stockAlarm 전체 진입·매도 로직이 KOSPI 매수후보유, watchlist 동일가중
매수후보유, 랜덤 종목선택, 단순 거래량 모멘텀보다 나은지를 같은 비용과
평가기간으로 비교합니다. 랜덤 전략은 기본 100회이며 시드를 기록합니다.
회전매매 전략은 라이브와 같은 `evaluate_risk_state` 위험중단 판정과
`correlation_limited_allocations` 고상관 연결그룹 40% 신규진입 제한을 사용합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.benchmark_comparison
```

결과는 `reports/backtest/BENCHMARK_COMPARISON_REPORT.md`에 저장되고, 원자료는
`reports/backtest/benchmark_comparison/`에 저장됩니다. 분석은 격리 OHLCV만
읽으며 라이브 DB·가상계좌·실주문 API를 사용하지 않습니다. 현재 watchlist를
과거에 적용하는 생존편향과 뉴스·공시·재무 point-in-time 자료 부재를 감안해
해석해야 합니다.

섹터 한도(`SECTOR_GROUP_MAX_PCT`, 기본 100으로 비활성)를 실험하려면 먼저
관심종목 업종 캐시를 갱신합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.sector_reference --refresh
```

### Point-in-Time 뉴스·공시·재무 데이터 수집

외부요인 원본은 라이브 DB와 분리된 `data/backtest/point_in_time.sqlite3`에
공개시각과 백테스트 가용시각을 함께 저장합니다. 같은 명령을 다시 실행하면
기본키 기준으로 캐시를 갱신하므로 중복 적재하지 않습니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.point_in_time_collect --start 2022-06-30 --sources news,disclosure,financial
```

네이버 검색 API는 날짜 범위 검색을 지원하지 않고 최신 1,000건까지만 조회되므로
오래된 뉴스는 미수집일 수 있습니다. OpenDART 목록의 날짜 전용 공시는 다음
영업일부터, PyKRX 일별 재무 스냅샷도 수정 이력 불확실성을 고려해 다음 영업일부터
사용합니다. 이 경로는 운영 설정·DB·가상계좌·실주문 API를 수정하지 않습니다.

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
