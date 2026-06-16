"""Phase 2 revenue estimation engine.

The engine is intentionally framework-free so the batch job, FastAPI, and the
static dashboard can share the same assumptions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal


Scenario = Literal["conservative", "base", "optimistic"]
ViewMode = Literal["day", "month"]
Region = Literal["LAND", "JEJU"]

SCENARIO_FACTOR: dict[Scenario, float] = {
    "conservative": 0.8,
    "base": 1.0,
    "optimistic": 1.2,
}

# KPX 제주 급전가능재생e/ESS '25/'26 적용 실효용량비율.
# Source: /home/opc/smp/docs/research_ESS.md
EFFECTIVE_CAPACITY_RATIO = {
    1: {"pv": 2.40, "wind": 28.66, "ess_2h": 27.75, "ess_4h": 48.30, "ess_6h": 65.70, "ess_8h": 73.41},
    2: {"pv": 2.18, "wind": 25.60, "ess_2h": 26.82, "ess_4h": 47.30, "ess_6h": 61.00, "ess_8h": 70.08},
    3: {"pv": 2.05, "wind": 22.15, "ess_2h": 30.50, "ess_4h": 57.02, "ess_6h": 71.10, "ess_8h": 78.13},
    4: {"pv": 3.18, "wind": 9.62, "ess_2h": 29.13, "ess_4h": 52.10, "ess_6h": 66.55, "ess_8h": 74.02},
    5: {"pv": 5.01, "wind": 11.92, "ess_2h": 30.79, "ess_4h": 58.72, "ess_6h": 72.88, "ess_8h": 79.41},
    6: {"pv": 7.75, "wind": 10.36, "ess_2h": 29.02, "ess_4h": 49.62, "ess_6h": 62.52, "ess_8h": 70.45},
    7: {"pv": 12.58, "wind": 17.55, "ess_2h": 39.23, "ess_4h": 72.24, "ess_6h": 85.00, "ess_8h": 90.71},
    8: {"pv": 13.04, "wind": 4.83, "ess_2h": 46.96, "ess_4h": 76.20, "ess_6h": 88.28, "ess_8h": 94.20},
    9: {"pv": 11.35, "wind": 11.06, "ess_2h": 44.36, "ess_4h": 73.07, "ess_6h": 85.59, "ess_8h": 91.03},
    10: {"pv": 6.20, "wind": 17.99, "ess_2h": 27.47, "ess_4h": 50.08, "ess_6h": 62.89, "ess_8h": 70.21},
    11: {"pv": 3.83, "wind": 24.27, "ess_2h": 29.96, "ess_4h": 53.58, "ess_6h": 67.61, "ess_8h": 75.91},
    12: {"pv": 4.00, "wind": 29.67, "ess_2h": 30.53, "ess_4h": 50.44, "ess_6h": 66.03, "ess_8h": 75.44},
    0: {"pv": 6.13, "wind": 17.81, "ess_2h": 32.71, "ess_4h": 57.39, "ess_6h": 71.26, "ess_8h": 78.58},
}


@dataclass
class RevenueInputs:
    region: Region = "JEJU"
    target_date: date | None = None
    scenario: Scenario = "base"
    view_mode: ViewMode = "day"
    pv_capacity_mw: float = 5.0
    wind_capacity_mw: float = 0.0
    generation_capacity_factor: float = 0.14
    ess_energy_mwh: float = 10.0
    ess_power_mw: float = 5.0
    ess_efficiency: float = 0.88
    subsidy_krw_per_kwh: float = 70.0
    rcp_krw_per_kw_h: float = 22.05
    rpcf: float = 1.0
    mape_pct: float = 8.0
    dispatch_instruction: bool = False
    bid_floor_krw_per_kwh: float = 0.0


def _month_key(target_date: date | None) -> int:
    return target_date.month if target_date else 0


def _ess_duration_bucket(energy_mwh: float, power_mw: float) -> str:
    if power_mw <= 0 or energy_mwh <= 0:
        return "ess_4h"
    duration = energy_mwh / power_mw
    if duration < 3:
        return "ess_2h"
    if duration < 5:
        return "ess_4h"
    if duration < 7:
        return "ess_6h"
    return "ess_8h"


def effective_capacity(inputs: RevenueInputs) -> dict[str, float | str]:
    ratios = EFFECTIVE_CAPACITY_RATIO[_month_key(inputs.target_date)]
    ess_bucket = _ess_duration_bucket(inputs.ess_energy_mwh, inputs.ess_power_mw)
    pv_mw = inputs.pv_capacity_mw * ratios["pv"] / 100.0
    wind_mw = inputs.wind_capacity_mw * ratios["wind"] / 100.0
    ess_mw = inputs.ess_power_mw * ratios[ess_bucket] / 100.0
    total_mw = pv_mw + wind_mw + ess_mw
    return {
        "month": _month_key(inputs.target_date) or "annual_average",
        "ess_bucket": ess_bucket,
        "pv_ratio_pct": ratios["pv"],
        "wind_ratio_pct": ratios["wind"],
        "ess_ratio_pct": ratios[ess_bucket],
        "pv_effective_mw": pv_mw,
        "wind_effective_mw": wind_mw,
        "ess_effective_mw": ess_mw,
        "total_effective_mw": total_mw,
    }


def ess_schedule(smp: list[float], power_mw: float) -> list[float]:
    if not smp or power_mw <= 0:
        return [0.0 for _ in smp]
    sorted_prices = sorted(smp)
    charge_threshold = sorted_prices[int(len(sorted_prices) * 0.25)]
    discharge_threshold = sorted_prices[min(len(sorted_prices) - 1, int(len(sorted_prices) * 0.78))]
    return [
        -power_mw if price <= charge_threshold else power_mw if price >= discharge_threshold else 0.0
        for price in smp
    ]


def estimate_revenue(smp: list[float], inputs: RevenueInputs) -> dict:
    prices = [float(v) for v in smp if v is not None]
    if not prices:
        raise ValueError("smp must contain at least one numeric value")
    if inputs.view_mode not in ("day", "month"):
        raise ValueError("view_mode must be day or month")
    if inputs.scenario not in SCENARIO_FACTOR:
        raise ValueError("unknown scenario")

    factor = SCENARIO_FACTOR[inputs.scenario]
    multiplier = 30.0 if inputs.view_mode == "month" else 1.0
    avg_price = sum(prices) / len(prices)
    low_price = min(prices)
    high_price = max(prices)

    generation_mwh_day = (
        (inputs.pv_capacity_mw + inputs.wind_capacity_mw)
        * 24.0
        * max(inputs.generation_capacity_factor, 0.0)
    )
    market_revenue = generation_mwh_day * 1000.0 * avg_price * multiplier * factor
    subsidy_revenue = generation_mwh_day * 1000.0 * inputs.subsidy_krw_per_kwh * multiplier * factor

    usable_ess_mwh = min(max(inputs.ess_energy_mwh, 0.0), max(inputs.ess_power_mw, 0.0) * 4.0)
    ess_revenue = usable_ess_mwh * 1000.0 * (high_price - low_price) * max(inputs.ess_efficiency, 0.0) * multiplier * factor

    ec = effective_capacity(inputs)
    capacity_revenue_day = (
        float(ec["total_effective_mw"])
        * 1000.0
        * inputs.rcp_krw_per_kw_h
        * 24.0
        * max(inputs.rpcf, 0.0)
    )
    capacity_revenue = capacity_revenue_day * multiplier * factor

    tolerance_pct = 1.5 if inputs.dispatch_instruction else 6.0
    excess_error_pct = max(inputs.mape_pct - tolerance_pct, 0.0)
    penalty_energy_kwh = generation_mwh_day * 1000.0 * excess_error_pct / 100.0
    imbalance_penalty = (
        penalty_energy_kwh
        * max(avg_price - inputs.bid_floor_krw_per_kwh, 0.0)
        * multiplier
    )

    total = market_revenue + ess_revenue + capacity_revenue + subsidy_revenue - imbalance_penalty
    schedule = ess_schedule(prices, inputs.ess_power_mw)
    outputs = {
        "prices": {
            "avg_krw_per_kwh": avg_price,
            "low_krw_per_kwh": low_price,
            "high_krw_per_kwh": high_price,
            "spread_krw_per_kwh": high_price - low_price,
        },
        "generation_mwh_day": generation_mwh_day,
        "effective_capacity": ec,
        "imbalance": {
            "tolerance_pct": tolerance_pct,
            "mape_pct": inputs.mape_pct,
            "excess_error_pct": excess_error_pct,
            "penalty_energy_kwh": penalty_energy_kwh,
        },
        "ess_schedule_mw": schedule,
        "market_revenue_krw": market_revenue,
        "ess_revenue_krw": ess_revenue,
        "capacity_revenue_krw": capacity_revenue,
        "subsidy_revenue_krw": subsidy_revenue,
        "imbalance_penalty_krw": imbalance_penalty,
        "total_revenue_krw": total,
    }
    return {"inputs": asdict(inputs), "outputs": outputs}

