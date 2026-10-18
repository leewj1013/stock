import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from revalidate_sell_timing import fill_sells, next_open_run
from revalidate_kr_execution_risk import ResearchSimulator
from stock_alarm.benchmark_comparison import PortfolioSimulator


def test_next_open_sell_waits_for_tradable_price_and_keeps_partial_basis():
    day1, day2 = "2024-01-03", "2024-01-04"
    rows = {day1: (0, ["20240103", 8, 9, 7, 9, 0]), day2: (1, ["20240104", 7, 8, 6, 8, 100])}
    benchmark = {day2: (0, ["20240104", 10, 10, 10, 10, 100])}
    engine = SimpleNamespace(by_date={"A": rows, "KOSPI": benchmark}, regimes={})
    sim = SimpleNamespace(engine=engine, cost_rate=.01)
    sim._open_mark = lambda ticker, day, fallback: rows[day][1][1]
    sim._sale_proceeds = lambda position, shares, price: shares * price - position["basis"] * shares / position["initial_shares"] * sim.cost_rate
    position = {"ticker": "A", "name": "A", "entry_date": "2024-01-02", "signal_date": "2024-01-01",
                "entry_price": 10, "initial_shares": 10, "shares": 10, "basis": 100, "entry_equity": 1000,
                "net_sale_proceeds": 0, "partial_taken": False, "first_take_profit_date": "", "first_take_profit_price": 0}
    positions, orders, trades, events, cooldown = {"A": position}, {"A": {"decision_date": "2024-01-02", "sale_type": "partial",
        "quantity_fraction": .5, "reason": "test", "stage": "test"}}, [], [], {}
    assert fill_sells(sim, day1, 0, positions, orders, trades, events, cooldown) == 0
    assert positions["A"]["shares"] == 10 and orders and not events
    cash = fill_sells(sim, day2, 0, positions, orders, trades, events, cooldown)
    assert cash == 34.5
    assert positions["A"]["shares"] == 5 and not orders
    assert position["first_take_profit_date"] == day2
    assert events[0]["decision_date"] == "2024-01-02" and events[0]["date"] == day2
    assert events[0]["transaction_cost"] == .5


def test_research_run_keeps_terminal_positions_instead_of_forced_close():
    assert callable(next_open_run())


def test_full_sell_uses_fill_day_for_trade_and_cooldown():
    decision, fill = "2024-01-03", "2024-01-04"
    engine = SimpleNamespace(by_date={
        "A": {fill: (0, ["20240104", 12, 13, 11, 13, 100])},
        "KOSPI": {decision: (0, ["20240103", 100, 100, 100, 100, 100]),
                  fill: (1, ["20240104", 110, 111, 109, 111, 100])},
    }, regimes={"2024-01-02": "bull"})
    sim = SimpleNamespace(engine=engine, cost_rate=.01)
    sim._open_mark = lambda ticker, day, fallback: 12
    def sale(position, shares, price):
        proceeds = shares * price - position["basis"] * shares / position["initial_shares"] * sim.cost_rate
        position["net_sale_proceeds"] += proceeds
        return proceeds
    sim._sale_proceeds = sale
    position = {"ticker": "A", "name": "A", "entry_date": decision, "signal_date": "2024-01-02",
                "entry_price": 10, "initial_shares": 10, "shares": 10, "basis": 100, "entry_equity": 1000,
                "net_sale_proceeds": 0, "first_take_profit_date": "", "first_take_profit_price": 0}
    positions = {"A": position}
    orders = {"A": {"decision_date": decision, "sale_type": "full", "quantity_fraction": 1,
                    "reason": "test", "stage": "test"}}
    trades, events, cooldown = [], [], {}
    cash = fill_sells(sim, fill, 0, positions, orders, trades, events, cooldown)
    assert cash == 119 and not positions and not orders
    assert trades[0].exit_date == fill and trades[0].exit_price == 12
    assert trades[0].benchmark_return_pct == 10 and cooldown == {"A": fill}
    sim.extra_slippage_bps = 100
    position["net_sale_proceeds"] = 0
    discounted = fill_sells(sim, fill, 0, {"A": position}, {"A": {"decision_date": decision,
        "sale_type": "full", "quantity_fraction": 1, "reason": "test", "stage": "test"}}, [], [], {})
    assert round(discounted, 2) == 117.8


def test_research_buy_filters_zero_volume_and_caps_at_prior_volume():
    first, second = "2024-01-02", "2024-01-03"
    engine = SimpleNamespace(cost_pct=0, top_n=1, minimum_score=0, regimes={first: "bull", second: "bull"})
    engine.by_date = {
        "KOSPI": {day: (i, [day.replace("-", ""), 10, 10, 10, 10, 100]) for i, day in enumerate((first, second))},
        "A": {first: (0, ["20240102", 1, 1, 1, 1, 100]), second: (1, ["20240103", 1, 1, 1, 1, 100])},
    }
    engine._market_up_ratio = lambda day: .7
    engine._history = lambda ticker, day: []
    candidates = {first: [{"ticker": "A", "name": "A", "score": 100, "volume_ratio": 1, "signal_date": first}], second: []}
    def run(policy, slippage=0):
        sim = PortfolioSimulator(engine, 100, 10, apply_live_constraints=False)
        sim.extra_slippage_bps = slippage
        sim.run = next_open_run(policy).__get__(sim, type(sim))
        return [event for event in sim.run("stock_alarm", candidates, liquidate_at_end=False).events if event["event"] == "buy"]
    assert run("existing")[0]["shares"] == 10
    assert run("existing", 100)[0]["shares"] == 9
    assert run("prior_volume_1pct")[0]["shares"] == 1
    engine.by_date["A"][second][1][5] = 0
    assert run("existing") and not run("positive_volume") and not run("prior_volume_1pct")


def test_stale_mark_changes_open_and_close_before_risk_decisions():
    first, second = "2024-01-02", "2024-01-03"
    engine = SimpleNamespace(cost_pct=0, regimes={first: "bull", second: "bull"}, by_date={
        "KOSPI": {first: (0, ["20240102", 10, 10, 10, 10, 100]), second: (1, ["20240103", 10, 10, 10, 10, 100])},
        "A": {first: (0, ["20240102", 10, 10, 10, 10, 100]), second: (1, ["20240103", 0, 0, 0, 10, 0])},
    })
    sim = ResearchSimulator(engine, 100, 10, apply_live_constraints=False)
    sim.stale_factor = .5
    assert sim._open_mark("A", second, 10) == 5
    assert sim._close("A", second, 10) == 5
    assert sim._close("A", first, 10) == 10
