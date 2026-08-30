from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .portfolio_risk import evaluate_risk_state


CONFIG_PATH = Path("config/risk_release_variants.json")
HARD_STOP_REASONS = {"daily_loss_limit", "weekly_loss_limit", "exposure_limit"}


def load_risk_release_variants(path: Path = CONFIG_PATH) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    variants = dict(payload.get("variants") or {})
    include_config = payload.get("include_config")
    if include_config:
        source_path = Path(include_config)
        included = load_risk_release_variants(source_path)
        names = payload.get("include_variants") or list(included)
        variants = {name: included[name] for name in names} | variants
    if not variants or "variant_A" not in variants:
        raise ValueError("risk release config must define variants including variant_A")
    common = dict(payload.get("common") or {})
    return {name: {**settings, "name": name, **common} for name, settings in variants.items()}


@dataclass
class DrawdownEpisode:
    active: bool = False
    released: bool = False
    start_day: str = ""
    start_equity: float = 0.0
    low_equity: float = 0.0
    trading_days: int = 0


class ExperimentalRiskController:
    """Stateful, backtest-only release policy around the shared live triggers.

    Trigger measurement always comes from ``evaluate_risk_state``. This class
    only changes how a drawdown episode is released. Daily, weekly, and exposure
    triggers remain hard stops under every experimental variant.
    """

    def __init__(self, policy: dict):
        self.policy = dict(policy)
        self.mode = str(self.policy.get("mode") or "baseline")
        if self.mode not in {"baseline", "rebound", "cooldown", "hysteresis", "reduced", "peak_decay"}:
            raise ValueError(f"Unknown risk release mode: {self.mode}")
        self.episode = DrawdownEpisode()
        self.previous_status = ""
        self.effective_high_water = 0.0
        self.last_decay_period: int | None = None
        self.decay_steps = 0

    def _period_index(self, created_at: datetime) -> int:
        frequency = str(self.policy.get("decay_frequency") or "monthly")
        if frequency == "monthly":
            return created_at.year * 12 + created_at.month - 1
        if frequency == "quarterly":
            return created_at.year * 4 + (created_at.month - 1) // 3
        interval = max(1, int(self.policy.get("decay_interval_days", 20)))
        return self.episode.trading_days // interval

    def _decay_peak(self, created_at: datetime) -> tuple[float, int]:
        if self.mode != "peak_decay" or not self.episode.active:
            return 0.0, 0
        period = self._period_index(created_at)
        if self.last_decay_period is None:
            self.last_decay_period = period
            return 0.0, 0
        steps = max(0, period - self.last_decay_period)
        if not steps:
            return 0.0, 0
        before = self.effective_high_water
        base_rate = abs(float(self.policy.get("decay_pct", 1.0))) / 100
        acceleration = abs(float(self.policy.get("acceleration_per_step_pct", 0.0))) / 100
        for _ in range(steps):
            rate = min(0.25, base_rate + acceleration * self.decay_steps)
            self.effective_high_water *= 1 - rate
            self.decay_steps += 1
        self.last_decay_period = period
        return max(0.0, before - self.effective_high_water), steps

    def evaluate(
        self,
        state: dict,
        daily_start_equity: float,
        weekly_start_equity: float,
        high_water: float,
        created_at: datetime,
    ) -> dict:
        equity_input = float(state.get("total_equity") or 0)
        if not self.effective_high_water:
            self.effective_high_water = max(float(high_water), equity_input)
        peak_reduction, decay_steps_today = self._decay_peak(created_at)
        evaluation_peak = self.effective_high_water if self.mode == "peak_decay" else high_water
        raw = evaluate_risk_state(
            state, daily_start_equity, weekly_start_equity, evaluation_peak,
            self.previous_status, created_at,
        )
        equity = float(raw["equity"])
        self.effective_high_water = float(raw["high_water"])
        raw_reasons = set(filter(None, str(raw["reason"]).split(",")))
        drawdown_active = "drawdown_limit" in raw_reasons
        hard_reasons = sorted(raw_reasons & HARD_STOP_REASONS)
        episode_transition = ""

        if drawdown_active and not self.episode.active:
            self.episode = DrawdownEpisode(True, False, created_at.date().isoformat(), equity, equity, 0)
            episode_transition = "drawdown_entered"
            self.last_decay_period = self._period_index(created_at)
            self.decay_steps = 0
        if self.episode.active:
            self.episode.trading_days += 1
            self.episode.low_equity = min(self.episode.low_equity or equity, equity)

        drawdown_halt = False
        reduced = False
        allocation_scale = 1.0
        if self.mode == "baseline":
            drawdown_halt = drawdown_active
        elif self.mode == "rebound" and self.episode.active and not self.episode.released:
            rebound_target = self.episode.low_equity * (1 + float(self.policy.get("rebound_pct", 5)) / 100)
            if equity >= rebound_target:
                self.episode.released = True
                episode_transition = "rebound_released"
            else:
                drawdown_halt = True
        elif self.mode == "cooldown" and self.episode.active:
            cooldown = max(1, int(self.policy.get("cooldown_days", 20)))
            if self.episode.trading_days >= cooldown:
                if not self.episode.released:
                    episode_transition = "cooldown_released"
                self.episode.released = True
                reduced = drawdown_active
                allocation_scale = float(self.policy.get("reentry_scale", 0.3)) if reduced else 1.0
            else:
                drawdown_halt = True
        elif self.mode == "hysteresis" and self.episode.active:
            release_level = -abs(float(self.policy.get("release_drawdown_pct", 5)))
            if float(raw["drawdown_pct"]) > release_level:
                self.episode.released = True
                episode_transition = "hysteresis_released"
            else:
                drawdown_halt = True
        elif self.mode == "reduced" and drawdown_active:
            reduced = True
            allocation_scale = float(self.policy.get("reentry_scale", 0.3))
            if self.episode.trading_days == 1:
                episode_transition = "reduced_entry"
        elif self.mode == "peak_decay":
            drawdown_halt = drawdown_active
            if peak_reduction > 0 and not drawdown_active:
                episode_transition = "peak_decay_released"

        should_clear_episode = self.episode.active and (
            (self.mode == "hysteresis" and self.episode.released)
            or (self.mode != "hysteresis" and not drawdown_active)
        )
        if should_clear_episode:
            if not episode_transition:
                episode_transition = "drawdown_cleared"
            self.episode = DrawdownEpisode()
            drawdown_halt = False
            reduced = False
            allocation_scale = 1.0

        if hard_reasons or drawdown_halt:
            status = "halted"
            allocation_scale = 0.0
        elif reduced:
            status = "reduced"
        else:
            status = "active"

        effective_reasons = list(hard_reasons)
        if drawdown_halt:
            effective_reasons.append("drawdown_limit")
        if status == "reduced":
            effective_reasons.append("drawdown_reduced_entry")
        if status == "halted" and self.previous_status != "halted":
            transition = "halted"
        elif status != "halted" and self.previous_status == "halted":
            transition = "resumed"
        else:
            transition = ""
        self.previous_status = status
        return {
            **raw,
            "status": status,
            "reason": ",".join(effective_reasons),
            "raw_reason": ",".join(sorted(raw_reasons)),
            "transition": transition,
            "allocation_scale": round(max(0.0, min(1.0, allocation_scale)), 4),
            "policy_name": self.policy.get("name", ""),
            "policy_mode": self.mode,
            "episode_transition": episode_transition,
            "episode_start_day": self.episode.start_day,
            "episode_start_equity": self.episode.start_equity,
            "episode_low_equity": self.episode.low_equity,
            "episode_trading_days": self.episode.trading_days,
            "episode_released": self.episode.released,
            "effective_high_water": round(self.effective_high_water, 4),
            "peak_decay_amount": round(peak_reduction, 4),
            "peak_decay_steps_today": decay_steps_today,
            "peak_decay_steps_total": self.decay_steps,
        }
