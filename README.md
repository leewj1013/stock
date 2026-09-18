# stockAlarm

국내주식 추천 후보, 보유종목 매도 검토, 아침 시황, 마감 브리핑을 텔레그램으로 보내고 가상계좌로 전략을 검증하는 로컬 자동화 프로젝트입니다.

투자 조언이 아니라 **검토할 후보와 위험 신호를 알려주는 감시 도구**입니다. 실제 계좌로는 주문을 내지 않으며(조회 전용), 최종 판단은 직접 해야 합니다.

## 문서 안내

| 문서 | 내용 |
|---|---|
| **이 문서** | 무엇이 언제 돌고, 어디서 무엇을 보는지 |
| [`STRATEGY_NOTES.md`](STRATEGY_NOTES.md) | 전략 결정 기록, 진행 중인 실험, 대기 후보 |
| [`REFERENCE.md`](REFERENCE.md) | 설정값을 **왜** 그 값으로 두는지 판단하는 개념과 근거 |
| [`.env.example`](.env.example) | 모든 설정값과 기본값 (설정 목록의 기준 문서) |
| `reports/README.md` | 백테스트·분석 보고서 색인 (로컬 전용, git 제외) |

## 빠른 사용법

```text
start_stock_alarm.bat  - 예약 작업 등록, 상태 점검, 대시보드 열기
open_dashboard.bat     - 대시보드 열기
issue_alert.bat        - 이슈 알림 수동 발송
set_dart_key.bat       - OpenDART API 키 등록
```

PC를 재부팅했거나 코드를 바꾼 뒤에는 `start_stock_alarm.bat`(또는 `scripts\register_daily_task.ps1`)을 다시 실행하세요. 예약 작업은 보호 런타임에 복사된 코드로 돌기 때문에, 다시 등록해야 새 코드가 반영됩니다.

## 설치

처음 한 번만 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
Copy-Item .env.example .env
Copy-Item data\positions.example.csv data\positions.csv
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\migrate_secrets.ps1
```

- **비밀값 분리:** `migrate_secrets.ps1`이 API 키·토큰을 `%USERPROFILE%\.stockAlarmSecure\secrets.env`로 옮기고 현재 사용자·SYSTEM·관리자만 읽도록 제한합니다. 이후 `.env`에는 비민감 설정만 둡니다. 민감 항목은 `.env`에 있어도 무시되고 보안 파일 값이 우선합니다.
- **보호 런타임:** `register_daily_task.ps1`이 소스·스크립트·가상환경을 `%USERPROFILE%\.stockAlarmSecure\runtime-*`에 복사해 잠근 뒤 모든 예약 작업을 그곳으로 연결합니다. DB·로그·보고서는 프로젝트 폴더를 그대로 씁니다. 이전 런타임은 자동 삭제하지 않습니다.
- **DB 스키마:** 앱 시작 시 자동으로 올라갑니다. 수동 실행은 `python -m stock_alarm.migrate_db`.

## 자동 실행

| 작업 | 시점 | 하는 일 |
|---|---|---|
| `stockAlarmOpen` | 매일 08:30 | 장전 시황 요약 발송 |
| `stockAlarmIntradayEvery5Minutes` | 평일 08:50~15:40, 5분마다 | 추천 → 가상매수(체결 시 섀도 기록) → 가상계좌 평가 → 대시보드 |
| `stockAlarmSellEvery5Minutes` | 평일 08:52~15:42, 5분마다 | 매도 조건 점검·가상매도 → 보유 수익률 기록 |
| `stockAlarmDaily` | 매일 16:00 | 보유 수익률 → 추천·매도 성과 → 학습 → **재무 스크리닝** → 마감 브리핑 → 점검 → 대시보드 → 이슈 알림 |
| `stockAlarmCollectStockWarnings` | 평일 16:10 | 토스 종목 경고(정리매매·투자위험 등) 기록 |
| `stockAlarmShadowTrader` | 평일 16:15 | 실계좌 보유종목의 매도(경고 종목) 섀도 기록 |
| `stockAlarmFinancialStatements` | 금요일 19:00 | DART 재무제표 전문 주간 수집 (약 30~40분) |
| `stockAlarmMaintenance` | 일요일 18:00 | DB 무결성 검사, 오래된 스냅샷 정리, 백업 |
| `stockAlarmDashboardServer` | 로그온 시 + 평일 장중 15분마다 | 로컬 대시보드 서버 기동·복구 |

- 실행 순서의 기준은 `scripts/run_stock_alarm.ps1`입니다. `performance` 모드는 추천·매도 성과만, `issue_alert` 모드는 이슈 알림만 돌립니다.
- 휴장일 판단: 08:30 `open`은 그날 시세가 아직 없어 `stock_alarm/run_gate.py`의 `KR_MARKET_HOLIDAYS` 목록을 씁니다(**매년 갱신 필요**, 한국거래소 공지 참고). 나머지는 실제 시세 발행 여부로 판단합니다.
- 상태 확인: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\status_daily_task.ps1`

## 텔레그램 알림

| 알림 | 시점 | 내용 |
|---|---|---|
| 장전 브리핑 | 08:30 | 시장 판단(공격·중립·방어)과 신규 매수 한도, 미국 ETF 마감, 국내 관심종목·전체 시장 흐름, 거래대금 주도 종목 |
| 가상매수 체결 | 장중, 체결 시 | 매수 수량·금액, **손절가·1차 익절가**, 신호, 재무 한 줄(PER·잉여현금흐름·매출 성장, 적자 경고) |
| 매도 알림 | 장중, 조건 충족 시 | 할 행동(전량/절반 매도)을 맨 위에, 수익률·사유·가상매도 결과·남은 수량 |
| 마감 브리핑 | 16:00 | 가상계좌 성과·시장 대비, 오늘 거래, **미매수 추천**, 섀도 요약, 재무 스크리닝 신규 통과 |
| 위험중단·해제 | 발생 시 | 계좌 손실 한도 도달로 신규 매수 중단/재개 |
| 장애·이슈 | 발생 시 | 작업 실패, 조치가 필요한 이슈 |

- **추천 알림은 가상매수가 체결됐을 때만** 보냅니다. 사지 않은 추천은 마감 브리핑에 모읍니다. 예전처럼 모든 추천을 받으려면 `.env`에 `RECOMMENDATION_ALERTS=all`.
- 추천·매도 알림은 거래일 장중에만 발송됩니다. 발송 기록은 `logs/deliveries.csv`, 중복 방지 키는 `logs/sent_keys.csv`.
- 설정: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`(보안 파일). 확인: `python -m stock_alarm.telegram_check`, 테스트 발송: `python -m stock_alarm.telegram_test`.

## 대시보드

`open_dashboard.bat`으로 엽니다(로컬 서버 `127.0.0.1:8765`, 정적 파일은 `reports/dashboard.html`).

| 탭 | 내용 |
|---|---|
| 홈 | 성향별 계좌 비교 카드, 운영 상태, 오늘 추천·매도 검토 종목 |
| 추천 추적 | 모든 추천의 이후 성과·매도 알림, **재무 스크리닝 결과** |
| 가상 트레이더 | **자산 추이 그래프**(입금 제외 수익률, KOSPI 비교), 계좌·보유종목(재무 열 포함)·매도 내역, **규칙 비교 실험** 표 |
| 실제 계좌 | 토스증권 계좌 조회(보유·주문·수수료), **섀도 주문 로그** |
| 시스템 관리 | 학습 표본 현황, 이슈, 실행 상태, 가격 데이터 품질, 검증 결과, 설정 |

보안: 전체 대시보드와 가상계좌 쓰기 API는 `127.0.0.1`에만 열리고, 입금·매수 요청은 같은 출처(`Origin`)여야 합니다. 원격 조회는 별도 읽기 전용 포트(`DASHBOARD_REMOTE_PORT`, 기본 8766)와 토큰으로만 가능하며, 전체 대시보드 포트는 외부 터널에 연결하지 않습니다. 원격 설정은 `scripts/start_remote_dashboard.ps1`, `scripts/open_remote_dashboard.ps1`.

## 가상계좌와 비교 실험

`stock_alarm/trading_profiles.py`에서 관리하며 계좌마다 DB가 분리돼 있습니다(`data/stock_alarm*.db`, **실제 입금액이 기록된 파일이므로 삭제 금지**).

| 계좌 | 역할 | 알림 |
|---|---|---|
| 적극투자형 (`stock_alarm.db`) | 기준 전략. 실거래 전환 대상 | 텔레그램 |
| 위험중립형 (`stock_alarm_neutral.db`) | 낮은 섹터·노출 한도, 타이트한 손절·익절 | 대시보드만 |
| 비교(현재 규칙) (`stock_alarm_exp_control.db`) | 적극투자형과 같은 규칙의 대조군, 1억 | 대시보드만 |
| 비교(후보 규칙) (`stock_alarm_exp_candidate.db`) | 상승장 익절 해제 + 낙폭중단 20일 후 30% 재진입, 1억 | 대시보드만 |
| 지수30%+전략70% (`stock_alarm_exp_core30.db`) | KODEX 200 30% + 비교(현재 규칙) 70%, 분기 리밸런싱 기록 | 대시보드만 |

비교 계좌들은 학습·가중치 검증에서 제외됩니다. 판단 시점과 기준은 `STRATEGY_NOTES.md`에 있습니다.

### 섀도 모드

실계좌 자동매매를 켜기 전에 "실계좌였다면 어떤 주문이 나갔을지"를 기록만 합니다. **실제 주문 API는 구현돼 있지 않습니다.**

- 매수: 적극투자형이 장중에 산 직후, 같은 시점의 시장 한도로 실계좌 기준 수량을 다시 계산합니다(체결 순서대로, 그날 앞선 섀도 매수는 예산에서 차감).
- 매도: 16:15에 실계좌 보유종목 중 종목 경고가 걸린 것을 기록합니다.
- 기록: DB `shadow_orders`, 제외된 종목까지 포함한 판정 로그 `logs/shadow_decisions.csv`.
- 실계좌 잔고가 없을 때는 `SHADOW_TRADER_ASSUMED_CAPITAL`(가정금액)로 계산합니다.

## 추천·매도 규칙 (요약)

**추천 후보군:** 관심종목(`data/watchlist.csv`) + 그날 거래대금 상위 종목(`DYNAMIC_SCREENING_TOP_N`). 시장 전체 상승비율이 `MIN_MARKET_UP_RATIO` 미만이면 그날은 추천하지 않습니다.

**필수 조건:** 거래량 급증(`VOLUME_MULTIPLIER`), 20일선 위, 최소 거래대금, 당일 과열·20일선 과도 이격 제외, 가격 데이터 품질 통과, 투자위험·정리매매 종목 제외. 이미 추적 중이거나 매도 후 냉각 기간인 종목은 다시 추천하지 않습니다.

**매수 비중:** 종목당 총자산 약 10% 목표, 고상관 종목 합산 40% 한도, 시장 상승비율에 따른 전체 보유 한도(100/40/10%).

**매도 조건:** 손절(고정 5%와 ATR×2 중 넓은 쪽), 20일선 2회 이탈, 수익 반납, 기간 청산(10거래일), 1차 익절 +10%(절반)·2차 익절 +20%(잔량), 종목 경고 발생 시 전량.

**위험중단:** 계좌가 일 −2%, 주 −5%, 고점 대비 −10%에 닿으면 신규 매수만 멈춥니다. 매도 감시는 계속됩니다.

**자동 학습:** 20거래일 성과가 확정된 추천 300건 이상부터 워크포워드·유의성 검증을 통과한 가중치만 적용합니다. 근거와 한계는 `REFERENCE.md` §1.

정확한 값은 `.env.example`이 기준입니다. 실제 적용값은 `.env`와 보안 파일이 덮어씁니다.

## 재무 데이터와 스크리닝

| 단계 | 명령 | 결과 |
|---|---|---|
| 재무제표 수집 (주 1회 자동) | `python -m stock_alarm.financial_statement_lines --dynamic-universe --stored --quarters 10` | `data/backtest/point_in_time.sqlite3`의 `financial_statement_lines` |
| 분기 지표 계산 | `python -m stock_alarm.financial_metrics` | `reports/fundamentals/quarterly_metrics.csv`, `ttm_and_dcf_inputs.csv` |
| 조건 검색 (매일 16:00 자동) | `python -m stock_alarm.screener` | `reports/fundamentals/screen_latest.json` (대시보드·브리핑이 읽음) |

- 스크리닝 조건은 `.env`의 `SCREENER_*` 설정으로 바꿉니다(기본: PER 15배 미만, 매출 성장률 5% 초과, 잉여현금흐름 양수). 값을 비우면 그 조건이 꺼집니다.
- PER·PBR·시장 구분은 KRX에서 받으며 `KRX_ID`·`KRX_PW`(보안 파일)가 필요합니다. 하루 한 번 `.cache/krx_fundamental.json`에 캐시합니다.
- EBITDA는 감가상각비를 공시하는 약 20% 종목에서만 계산됩니다.

## 외부 연동

- **토스증권 Open API**(`TOSS_CLIENT_ID`·`TOSS_CLIENT_SECRET`): 실계좌 조회, 장중 가격 교차검증, 종목 경고, 일봉. 토스 WTS에서 이 PC의 공인 IP를 허용해야 합니다. 확인: `python -m stock_alarm.toss_check`.
- **OpenDART**(`DART_API_KEY`, `set_dart_key.bat`): 공시, 재무제표. 없어도 기본 기능은 동작합니다.
- **네이버 금융**: 일별 시세(기본 데이터 소스), 모바일 API로 PER 등.
- **KRX**: 전체 시장 상승비율, 거래대금 상위 종목, 전 종목 PER.
- **Alpha Vantage**(`ALPHA_VANTAGE_API_KEY`): 미국 ETF(SPY·QQQ·SOXX·IWM) 마감.

## 로그와 데이터 위치

```text
logs/task.out.log / task.err.log      예약 작업 출력·오류
logs/errors.log                       앱 오류
logs/recommendations.csv              추천 기록
logs/sell_alerts*.csv                 계좌별 매도 알림
logs/positions_report.csv             보유 수익률 스냅샷
logs/deliveries.csv, sent_keys.csv    알림 발송·중복 방지
logs/shadow_decisions.csv             섀도 매수 판정(제외 포함)
logs/financial_statements.log         주간 재무제표 수집
data/stock_alarm*.db                  계좌별 운영 DB (삭제 금지)
data/backtest/point_in_time.sqlite3   뉴스·공시·재무 시점 데이터
reports/                              대시보드, 분석 보고서 (git 제외)
```

운영 DB에는 판단 원자료도 남습니다: `strategy_runs`(실행·설정), `candidate_snapshots`(전 후보의 지표·탈락 사유), `position_checks`(보유종목 판단). 로그 정리는 `python -m stock_alarm.cleanup_logs`(미리보기) / `--apply`.

## 수동 실행

일반 운영은 배치 파일로 충분합니다. 문제 확인·개발용입니다(`.\.venv\Scripts\python -m ...`).

| 목적 | 모듈 |
|---|---|
| 상태 점검 | `stock_alarm.health` |
| 추천 1회 | `stock_alarm` |
| 매도 점검 1회 | `stock_alarm.sell_check` |
| 장전 브리핑 / 마감 브리핑 | `stock_alarm.market_summary` / `stock_alarm.daily_summary` |
| 추천 성과 계산 | `stock_alarm.recommendation_performance` |
| 대시보드 생성 | `stock_alarm.dashboard` |
| 섀도 매도 점검 | `stock_alarm.shadow_trader` |

## 백테스트·검증 도구

모두 라이브 DB·가상계좌·실주문 API를 건드리지 않고 격리된 데이터만 씁니다. 결과는 `reports/backtest/`에 저장되며 목록은 `reports/README.md`에 있습니다.

| 모듈 | 용도 |
|---|---|
| `stock_alarm.backtest_data` | 2022년 이후 관심종목·KOSPI 일봉 수집과 품질 검사 |
| `stock_alarm.validation_backtest` | 추천·매도·분할익절 워크포워드 검증 |
| `stock_alarm.benchmark_comparison` | KOSPI 보유·동일가중·랜덤·단순 모멘텀 대비 비교 |
| `stock_alarm.weight_variant_backtest` | 요인 가중치 변형 비교 |
| `stock_alarm.point_in_time_collect` | 뉴스·공시·재무 시점 데이터 수집 |
| `stock_alarm.sector_reference --refresh` | 업종 매핑 갱신 (섹터 한도 실험용) |

해석 시 주의: 현재 관심종목을 과거 전체에 적용하므로 **생존편향**이 있고, p-value 통과가 과최적화 방지를 보장하지 않습니다.

## 커밋 전 확인

공개 파일에 토큰·API 키가 들어갔는지 확인합니다.

```powershell
.\.venv\Scripts\python -m stock_alarm.preflight
```
