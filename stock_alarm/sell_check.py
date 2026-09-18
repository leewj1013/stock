from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean

from .app import (
    env_float,
    average_true_range_pct,
    current_market_regime,
    is_market_alert_time,
    latest_naver_trading_day,
    latest_sell_alert_times,
    load_env,
    naver_rows,
    parse_time,
    sell_cooldown_days,
    stock_name,
    write_error_log,
)
from .data_store import position_id


POSITIONS_PATH = "data/positions.csv"
SELL_ALERTS_LOG = "logs/sell_alerts.csv"
POSITIONS_REPORT_LOG = "logs/positions_report.csv"


@dataclass(frozen=True)
class SellAlert:
    ticker: str
    name: str
    entry_price: int
    close: int
    return_pct: float
    reason: str
    holding_days: int | None = None
    sale_type: str = "full"
    stage: str = ""
    quantity_fraction: float = 1.0


def read_positions(path: str = POSITIONS_PATH) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as file:
        return [row for row in csv.DictReader(file) if row.get("ticker") and row.get("entry_price")]


def active_positions(path: str = POSITIONS_PATH) -> list[dict[str, str]]:
    """read_positions(), collapsed to the latest row per ticker.

    The file is append-only (track_positions() never removes a row), and
    only ever adds a new row for a ticker once its prior entry is inactive
    -- so at most one row per ticker is ever the current position. Without
    this, a stale earlier entry left in the file gets evaluated alongside
    the real current one and can produce a duplicate sell alert for the
    same position. positions_check.py's validator uses read_positions()
    directly since it needs to see every raw row, duplicates included.
    """
    latest: dict[str, dict[str, str]] = {}
    for row in read_positions(path):
        latest[row["ticker"].strip()] = row
    return list(latest.values())


def virtual_holding_positions(state: dict) -> list[dict[str, str]]:
    """Convert one profile's actual virtual holdings into sell-check inputs."""
    return [
        {
            "ticker": str(holding["ticker"]),
            "name": str(holding.get("name") or holding["ticker"]),
            "entry_price": str(holding["average_price"]),
            "entry_date": str(holding.get("first_entry_at") or ""),
        }
        for holding in state.get("holdings", [])
        if holding.get("ticker") and float(holding.get("average_price") or 0) > 0
    ]


def _evaluate_position(
    position: dict[str, str],
    end_day: date,
    previous_return: float | None,
    max_return: float | None,
    price_rows: list[list] | None,
    partial_taken: bool,
    remaining_quantity: int,
    sell_policy: dict | None,
) -> dict | None:
    """Single source of truth for sell-trigger evaluation.

    check_position() and position_snapshot() both need these same trigger
    booleans; computing them once here keeps the live alert and the persisted
    audit row from drifting apart.
    """
    ticker = position["ticker"].strip()
    entry_price = int(float(position["entry_price"]))
    # price_rows is only ever passed explicitly by the point-in-time backtest
    # (validation_backtest.py); the live path always leaves it None and fetches
    # naver_rows() itself. Reused here to gate the live-only Toss warning
    # lookup below -- a backtest simulating a past day must never ask Toss
    # for *today's* warning status.
    is_live = price_rows is None
    rows = price_rows if price_rows is not None else naver_rows(ticker, end_day - timedelta(days=90), end_day)
    if len(rows) < 20 or entry_price <= 0:
        return None

    closes = [int(row[4]) for row in rows[-20:]]
    close = closes[-1]
    ma20 = mean(closes)
    return_pct = (close - entry_price) / entry_price * 100
    atr20_pct = average_true_range_pct(rows)
    policy = sell_policy or {}
    fixed_stop_pct = float(policy.get("stop_loss_pct", env_float("SELL_LOSS_PCT", 5)))
    atr_stop_multiplier = float(policy.get("stop_atr_multiplier", env_float("SELL_ATR_MULTIPLIER", 2)))
    stop_loss_pct = -max(abs(fixed_stop_pct), atr20_pct * atr_stop_multiplier)
    reasons: list[str] = []
    if is_live:
        from .toss_client import blocking_warnings_for
        blocking = blocking_warnings_for(ticker)
        if blocking:
            # Holding through a liquidation-trading/investment-risk
            # designation is riskier than any price-based reason to stay in,
            # so this fires regardless of stop-loss/take-profit state.
            reasons.append(f"종목 경고 발생: {','.join(sorted(blocking))}")
    stop_triggered = return_pct <= stop_loss_pct
    if stop_triggered:
        reasons.append(f"손절 기준 {stop_loss_pct:.1f}% 이탈")
    confirm_days = max(1, int(policy.get("ma_confirm_days", 2)))
    fixed_band_pct = max(0.0, float(policy.get("ma_band_pct", 0.0)))
    atr_band_multiplier = max(0.0, float(policy.get("ma_atr_band_multiplier", 0.0)))
    band_pct = max(fixed_band_pct, atr20_pct * atr_band_multiplier)
    ma_breaks = []
    for offset in range(confirm_days):
        cursor = len(rows) - 1 - offset
        if cursor < 19:
            ma_breaks.append(False)
            continue
        window = [int(item[4]) for item in rows[cursor - 19:cursor + 1]]
        session_ma20 = mean(window)
        ma_breaks.append(int(rows[cursor][4]) < session_ma20 * (1 - band_pct / 100))
    ma20_triggered = len(ma_breaks) == confirm_days and all(ma_breaks)
    if ma20_triggered:
        band_text = f", 완충 {band_pct:.2f}%" if band_pct else ""
        reasons.append(f"20일선 {confirm_days}회 연속 이탈{band_text}")
    drop_triggered = False
    if previous_return is not None:
        drop = previous_return - return_pct
        if drop >= env_float("SELL_DROP_PCT", 3) and (ma20_triggered or stop_triggered):
            reasons.append(f"직전 평가 대비 수익률 {drop:.1f}%p 악화")
            drop_triggered = True
    giveback_triggered = False
    if max_return is not None and max_return >= env_float("SELL_PROTECT_PROFIT_PCT", 5):
        giveback = max_return - return_pct
        if giveback >= env_float("SELL_GIVEBACK_PCT", 4) and (ma20_triggered or stop_triggered):
            reasons.append(f"고점 수익률 {max_return:.1f}% 대비 {giveback:.1f}%p 반납")
            giveback_triggered = True
    entry_time = parse_time(position.get("entry_date", ""))
    holding_days = (end_day - entry_time.date()).days if entry_time else None
    time_stop_triggered = False
    if holding_days is not None and holding_days >= int(env_float("SELL_TIME_STOP_DAYS", 10)) and return_pct <= env_float("SELL_TIME_STOP_MIN_RETURN_PCT", 0):
        reasons.append(f"{holding_days}일 보유 후 기대수익 미달")
        time_stop_triggered = True
    sale_type, stage, quantity_fraction = "full", "", 1.0
    take_profit_1_pct = float(policy.get("take_profit_1_pct", env_float("TAKE_PROFIT_1_PCT", 10)))
    take_profit_2_pct = float(policy.get("take_profit_2_pct", env_float("TAKE_PROFIT_2_PCT", 20)))
    take_profit_1_sell_ratio = float(policy.get("take_profit_1_sell_ratio", env_float("TAKE_PROFIT_1_SELL_RATIO", 50)))
    # Initial thresholds are conservative placeholders and must be tuned by
    # backtest before any real-account integration.
    take_profit_allowed = policy.get("market_regime") not in set(policy.get("disable_take_profit_in_regimes") or ())
    if take_profit_allowed and not reasons and partial_taken and return_pct >= take_profit_2_pct:
        reasons.append(f"2차 익절 목표 +{take_profit_2_pct:.1f}% 도달")
        stage = "take_profit_2"
    elif take_profit_allowed and not reasons and not partial_taken and remaining_quantity == 1 and return_pct >= take_profit_2_pct:
        reasons.append(f"정수수량 제약으로 2차 익절 목표 +{take_profit_2_pct:.1f}%에서 1주 전량 매도")
        stage = "take_profit_2"
    elif take_profit_allowed and not reasons and not partial_taken and remaining_quantity >= 2 and return_pct >= take_profit_1_pct:
        reasons.append(f"1차 익절 목표 +{take_profit_1_pct:.1f}% 도달")
        sale_type = "partial"
        stage = "take_profit_1"
        quantity_fraction = max(0.01, min(0.99, take_profit_1_sell_ratio / 100))
    return {
        "ticker": ticker, "entry_price": entry_price, "close": close, "ma20": ma20,
        "return_pct": return_pct, "atr20_pct": atr20_pct, "stop_loss_pct": stop_loss_pct,
        "stop_triggered": stop_triggered, "ma20_triggered": ma20_triggered,
        "drop_triggered": drop_triggered, "giveback_triggered": giveback_triggered,
        "holding_days": holding_days, "time_stop_triggered": time_stop_triggered,
        "reasons": reasons, "sale_type": sale_type, "stage": stage, "quantity_fraction": quantity_fraction,
    }


def _alert_from_evaluation(position: dict[str, str], evaluation: dict) -> SellAlert | None:
    if not evaluation["reasons"]:
        return None
    ticker = evaluation["ticker"]
    name = stock_name(ticker, position.get("name", ticker).strip() or ticker)
    return SellAlert(
        ticker, name, evaluation["entry_price"], evaluation["close"], evaluation["return_pct"],
        ", ".join(evaluation["reasons"]), evaluation["holding_days"],
        evaluation["sale_type"], evaluation["stage"], evaluation["quantity_fraction"],
    )


def check_position(
    position: dict[str, str],
    end_day: date,
    previous_return: float | None = None,
    max_return: float | None = None,
    price_rows: list[list] | None = None,
    partial_taken: bool = False,
    remaining_quantity: int = 0,
    sell_policy: dict | None = None,
) -> SellAlert | None:
    evaluation = _evaluate_position(position, end_day, previous_return, max_return, price_rows, partial_taken, remaining_quantity, sell_policy)
    if evaluation is None:
        return None
    return _alert_from_evaluation(position, evaluation)


def _position_returns(path: str, maximum: bool) -> dict[str, float]:
    if not os.path.exists(path):
        return {}
    result: dict[str, float] = {}
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            key = (row.get("position_id") or "").strip()
            if not key:
                continue
            value = float(row.get("return_pct") or 0)
            result[key] = max(result.get(key, float("-inf")), value) if maximum else value
    return result


def previous_returns(path: str = POSITIONS_REPORT_LOG) -> dict[str, float]:
    return _position_returns(path, maximum=False)


def max_returns(path: str = POSITIONS_REPORT_LOG) -> dict[str, float]:
    return _position_returns(path, maximum=True)


def alerted_tickers(path: str = SELL_ALERTS_LOG) -> set[str]:
    return set(latest_sell_alert_times(path))


def position_was_alerted(position: dict[str, str], path: str = SELL_ALERTS_LOG) -> bool:
    ticker = position.get("ticker", "").strip()
    sell_time = latest_sell_alert_times(path).get(ticker)
    if not sell_time:
        return False
    entry_time = parse_time(position.get("entry_date", ""))
    # Legacy positions without an entry date retain the conservative old behavior.
    return entry_time is None or entry_time <= sell_time


def position_snapshot(
    position: dict[str, str],
    end_day: date,
    previous_return: float | None = None,
    max_return: float | None = None,
    partial_taken: bool = False,
    remaining_quantity: int = 0,
    sell_policy: dict | None = None,
) -> tuple[SellAlert | None, dict]:
    ticker = position["ticker"].strip()
    entry_price = int(float(position["entry_price"]))
    base = {
        "position_id": position_id(position),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "name": position.get("name", ticker),
        "entry_date": position.get("entry_date", ""),
        "entry_price": entry_price,
        "previous_return_pct": previous_return,
        "max_return_pct": max_return,
        "atr20_pct": None,
        "dynamic_stop_loss_pct": None,
        "stop_loss_triggered": 0,
        "ma20_break_triggered": 0,
        "return_drop_triggered": 0,
        "giveback_triggered": 0,
        "time_stop_triggered": 0,
        "take_profit_triggered": 0,
        "sale_type": "full",
        "stage": "",
        "decision": "NO_DATA",
        "reasons": "insufficient_history",
    }
    evaluation = _evaluate_position(position, end_day, previous_return, max_return, None, partial_taken, remaining_quantity, sell_policy)
    if evaluation is None:
        return None, base

    alert = _alert_from_evaluation(position, evaluation)
    return alert, {
        **base,
        "name": alert.name if alert else position.get("name", ticker),
        "close": evaluation["close"],
        "holding_days": evaluation["holding_days"],
        "return_pct": evaluation["return_pct"],
        "drawdown_from_peak_pct": None if max_return is None else max_return - evaluation["return_pct"],
        "ma20": evaluation["ma20"],
        "atr20_pct": evaluation["atr20_pct"],
        "dynamic_stop_loss_pct": evaluation["stop_loss_pct"],
        "distance_ma20_pct": (evaluation["close"] / evaluation["ma20"] - 1) * 100 if evaluation["ma20"] else None,
        "stop_loss_triggered": int(evaluation["stop_triggered"]),
        "ma20_break_triggered": int(evaluation["ma20_triggered"]),
        "return_drop_triggered": int(evaluation["drop_triggered"]),
        "giveback_triggered": int(evaluation["giveback_triggered"]),
        "time_stop_triggered": int(evaluation["time_stop_triggered"]),
        "take_profit_triggered": int(bool(alert and alert.stage.startswith("take_profit"))),
        "sale_type": evaluation["sale_type"],
        "stage": evaluation["stage"],
        "decision": "SELL" if alert else "HOLD",
        "reasons": ", ".join(evaluation["reasons"]),
    }


def find_alerts(
    positions: list[dict[str, str]],
    end_day: date,
    run_id: str | None = None,
    virtual_states: dict[str, dict] | None = None,
    virtual_quantities: dict[str, int] | None = None,
    sell_policy: dict | None = None,
    alerted_log_path: str = SELL_ALERTS_LOG,
) -> list[SellAlert]:
    previous = previous_returns()
    best = max_returns()
    alerts: list[SellAlert] = []
    snapshots: list[dict] = []
    virtual_states = virtual_states or {}
    virtual_quantities = virtual_quantities or {}
    for position in positions:
        ticker = position["ticker"].strip()
        key = position_id(position)
        if position_was_alerted(position, path=alerted_log_path):
            if run_id:
                snapshots.append({
                    "position_id": key,
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                    "ticker": ticker,
                    "name": position.get("name", ticker),
                    "entry_date": position.get("entry_date", ""),
                    "entry_price": int(float(position["entry_price"])),
                    "stop_loss_triggered": 0,
                    "ma20_break_triggered": 0,
                    "return_drop_triggered": 0,
                    "giveback_triggered": 0,
                    "decision": "ALREADY_ALERTED",
                    "reasons": "sell_alert_after_entry",
                })
            continue
        if run_id:
            state = virtual_states.get(ticker, {})
            alert, snapshot = position_snapshot(
                position, end_day, previous.get(key), best.get(key),
                state.get("status") == "partial", int(virtual_quantities.get(ticker, 0)), sell_policy,
            )
            snapshots.append(snapshot)
        else:
            state = virtual_states.get(ticker, {})
            alert = check_position(
                position, end_day, previous.get(key), best.get(key), None,
                state.get("status") == "partial", int(virtual_quantities.get(ticker, 0)), sell_policy,
            )
        if alert:
            alerts.append(alert)
    if run_id:
        from .data_store import write_position_checks

        write_position_checks(run_id, snapshots)
    return alerts


def alert_summary(alert: SellAlert) -> str:
    if alert.stage == "take_profit_1":
        return "1차 부분익절"
    if alert.stage == "take_profit_2":
        return "2차 잔량익절"
    if "반납" in alert.reason:
        return "고점 대비 수익 반납"
    if alert.return_pct <= -abs(env_float("SELL_LOSS_PCT", 5)):
        return f"손실 {alert.return_pct:.1f}%"
    if "20일선" in alert.reason:
        return "20일선 이탈"
    return "수익률 악화"


def alert_urgency(alert: SellAlert) -> str:
    if alert.stage == "take_profit_1":
        return "🟢 1차 부분익절"
    if alert.stage == "take_profit_2":
        return "🟢 2차 잔량익절"
    if "손절 기준" in alert.reason:
        return "🔴 자동 매도"
    if "반납" in alert.reason:
        return "🟠 수익 보호 매도"
    if "보유 후" in alert.reason:
        return "⚪ 기간 청산"
    return "🟡 추세 이탈 매도"


def format_message(alerts: list[SellAlert], virtual_result: dict | None = None) -> str:
    if not alerts:
        return "오늘 매도 검토 조건에 걸린 보유 종목이 없습니다."
    lines = [f"[매도 알림 · {datetime.now().strftime('%H:%M')}] {len(alerts)}건"]
    executions = {row["ticker"]: row for row in (virtual_result or {}).get("executions", [])}
    remaining = {str(row.get("ticker")): int(row.get("quantity") or 0) for row in (virtual_result or {}).get("holdings", [])}
    for alert in alerts:
        execution = executions.get(alert.ticker)
        action = "절반 매도" if alert.sale_type == "partial" else "전량 매도"
        held = f" · 보유 {alert.holding_days}일" if alert.holding_days is not None else ""
        lines.extend([
            "",
            f"{alert_urgency(alert)} · {action}",
            f"{alert.name}({alert.ticker}) 수익률 {alert.return_pct:+.2f}%{held}",
            f"현재가 {alert.close:,}원 (진입 {alert.entry_price:,}원)",
            f"사유: {alert.reason}",
        ])
        if execution:
            realized_rate = execution["realized_profit_loss"] / execution["cost_basis"] * 100 if execution["cost_basis"] else 0
            label = "부분매도" if execution.get("sale_type") == "partial" else "전량매도"
            line = (f"가상 {label} 완료: {execution['quantity']:,}주 · "
                    f"실현손익 {execution['realized_profit_loss']:+,}원({realized_rate:+.2f}%)")
            if remaining.get(alert.ticker):
                line += f" · 남은 {remaining[alert.ticker]:,}주"
            lines.append(line)
    if virtual_result and virtual_result.get("sold"):
        lines.extend(["", f"매도 후 현금: {virtual_result.get('cash', 0):,}원"])
    lines.append("조건 기반 매도 검토 알림이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def write_log(alerts: list[SellAlert], path: str = SELL_ALERTS_LOG) -> None:
    from .csv_schema import ensure_header, migrate_sell_alert_row

    header = ["created_at", "ticker", "name", "entry_price", "close", "return_pct", "summary", "reason", "sale_type", "stage", "quantity_fraction"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    ensure_header(path, header, migrate_sell_alert_row)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(header)
        for alert in alerts:
            writer.writerow([datetime.now().isoformat(timespec="seconds"), alert.ticker, alert.name, alert.entry_price, alert.close, f"{alert.return_pct:.2f}", alert_summary(alert), alert.reason, alert.sale_type, alert.stage, f"{alert.quantity_fraction:.4f}"])


def _run_profile_sell_check(positions: list[dict[str, str]], end_day: date, run_id: str | None, profile: dict) -> tuple[list[SellAlert], dict]:
    from .data_store import virtual_position_states, virtual_trader_state, virtual_sell
    state = virtual_trader_state(path=profile["db_path"])
    if profile.get("sell_alerts_log") != SELL_ALERTS_LOG:
        positions = virtual_holding_positions(state)
    quantities = {holding["ticker"]: int(holding["quantity"]) for holding in state["holdings"]}
    sell_policy = profile["sell_policy"]
    if sell_policy and sell_policy.get("disable_take_profit_in_regimes"):
        # Previous session's regime, matching the backtest that validated this
        # rule -- today's label isn't final until the close.
        sell_policy = {**sell_policy, "market_regime": current_market_regime(end_day - timedelta(days=1))}
    alerts = find_alerts(
        positions, end_day, run_id,
        virtual_position_states(path=profile["db_path"]), quantities,
        sell_policy, profile["sell_alerts_log"],
    )
    if alerts:
        write_log(alerts, path=profile["sell_alerts_log"])
    result = virtual_sell([
        {"ticker": alert.ticker, "name": alert.name, "close": alert.close, "reason": alert.reason,
         "sale_type": alert.sale_type, "stage": alert.stage, "quantity_fraction": alert.quantity_fraction}
        for alert in alerts
    ], path=profile["db_path"])
    return alerts, result


def run() -> str:
    load_env()
    if not is_market_alert_time():
        return "market_closed"
    from .data_store import finish_run, start_run
    from .trading_profiles import PROFILES

    end_day = latest_naver_trading_day()
    run_id = start_run("sell_check", end_day.isoformat())
    positions = active_positions()
    try:
        alerts, virtual_result = _run_profile_sell_check(positions, end_day, run_id, PROFILES["aggressive"])
        finish_run(run_id)
    except Exception:
        finish_run(run_id, "failed")
        raise

    # Secondary profiles are comparison-only: they share the aggressive
    # account's run_id/audit trail only when it's their turn to log
    # (run_id=None below), so a failure here can't mark the primary sell
    # run as failed or block its already-completed alerts/execution.
    for name, profile in PROFILES.items():
        if name == "aggressive":
            continue
        try:
            _run_profile_sell_check(positions, end_day, None, profile)
        except Exception as error:
            write_error_log(error)

    if not alerts and os.environ.get("SEND_EMPTY_SELL_ALERT", "0") != "1":
        return "no_alerts"
    from .notifier import send_notification

    return send_notification(format_message(alerts, virtual_result), event_type="sell", tickers=[alert.ticker for alert in alerts])


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
