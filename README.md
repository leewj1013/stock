# stockAlarm

국내주식 추천 후보와 보유 종목 매도 검토 알림을 텔레그램으로 보내는 로컬 자동화 프로젝트입니다.

이 프로젝트는 투자 조언이 아니라 “검토할 만한 후보를 알려주는 감시 도구”입니다. 최종 매수/매도 판단은 직접 확인해야 합니다.

## 가장 쉬운 사용법

평소에는 아래 배치 파일만 사용하면 됩니다.

```text
start_stock_alarm.bat  - 서버/스케줄 시작, 상태 점검, 대시보드 열기
open_dashboard.bat     - 대시보드 새로고침 후 열기
open_check.bat         - 오전 08:55 추천 실행 결과 확인
issue_alert.bat        - 현재 문제 내역만 텔레그램으로 발송
set_dart_key.bat       - OpenDART API 키 등록
```

컴퓨터를 재부팅했거나 자동 실행이 의심되면 `start_stock_alarm.bat`을 더블클릭하세요.

## 자동 실행 시간

`start_stock_alarm.bat`을 실행하면 Windows 작업 스케줄러에 아래 작업이 등록됩니다.

```text
08:55  장 시작 전 추천 후보 발송
10:30  장중 보유/매도 검토
13:30  장중 보유/매도 검토
15:00  장중 보유/매도 검토
16:10  장마감 후 추천, 매도 검토, 성과 계산, 요약, 대시보드 갱신
```

스케줄 상태를 직접 확인하려면:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\status_daily_task.ps1
```

## 대시보드

대시보드는 한 파일로 관리됩니다.

```text
reports/dashboard.html
```

대시보드를 새로 만들고 열려면 `open_dashboard.bat`을 더블클릭하세요.

대시보드에서 확인할 수 있는 내용:

- 오늘 추천 종목
- 추천 점수와 추천 사유
- 장중 매도 검토 내역
- 추천 성과 통계
- 성과 상위/하위 추천 종목
- 보유 종목 수익률
- 최근 발송 내역
- 작업 오류 내역

## 추천 기준

현재 추천 규칙은 단순한 룰 기반입니다.

- 오늘 거래량이 직전 20거래일 평균의 1.5배 이상
- 현재가가 20일 이동평균 위
- 거래대금 50억 원 이상
- 당일 가격 변동폭 8% 이하
- 최근 평균 장중 변동폭 12% 이하
- 추천 점수 50점 이상
- 이미 추천되어 추적 중인 종목은 매도 알림이 나올 때까지 중복 추천 제외
- 상위 5개 후보 발송

점수는 최대 100점입니다.

```text
거래량 급증        45점
거래대금           35점
20일선 대비 위치   20점
```

성과 통계가 쌓이면 점수 기준은 나중에 확장할 수 있습니다. 현재는 뉴스, 공시, 성과 감점 항목을 붙일 수 있는 구조만 준비되어 있습니다.

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

봇 토큰만 확인:

```powershell
.\.venv\Scripts\python -m stock_alarm.telegram_check
```

발송 기록은 아래 파일에 쌓입니다.

```text
logs/deliveries.csv
```

## OpenDART 설정

OpenDART API 키를 받으면 `set_dart_key.bat`을 더블클릭해서 등록하세요.

등록 후 특정 종목 공시 점검:

```powershell
.\.venv\Scripts\python -m stock_alarm.dart_reference 005930
```

OpenDART 키가 없어도 기본 추천/텔레그램/대시보드는 동작합니다.

## 설치

처음 한 번만 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item data\positions.example.csv data\positions.csv
```

그 다음 `.env`에 텔레그램 값을 입력하세요.

## 주요 로그 파일

```text
logs/recommendations.csv                    추천 후보 기록
logs/sell_alerts.csv                        매도 검토 알림 기록
logs/positions_report.csv                   보유 종목 수익률 기록
logs/recommendation_performance.csv         추천 성과 상세
logs/recommendation_performance_summary.csv 추천 성과 요약
logs/task.out.log                           자동 실행 출력 로그
logs/task.err.log                           자동 실행 오류 로그
logs/errors.log                             앱 오류 로그
```

## 수동 실행이 필요할 때

일반 운영은 배치 파일로 충분합니다. 아래 명령은 문제 확인이나 개발할 때만 사용하세요.

상태 점검:

```powershell
.\.venv\Scripts\python -m stock_alarm.health
```

추천 후보 수동 실행:

```powershell
.\.venv\Scripts\python -m stock_alarm
```

매도 검토 수동 실행:

```powershell
.\.venv\Scripts\python -m stock_alarm.sell_check
```

일일 요약 수동 발송:

```powershell
.\.venv\Scripts\python -m stock_alarm.daily_summary
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

공개 파일에 토큰이나 키가 섞였는지 확인합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.preflight
```
