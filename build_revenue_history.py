#!/usr/bin/env python3
"""Build daily ESS/VPP revenue records from forecast and actual SMP artifacts."""
from __future__ import annotations

import argparse
import math
import re
from datetime import date
from pathlib import Path

import pandas as pd

from api.revenue_engine import RevenueInputs, estimate_revenue


FORECAST_RE = re.compile(r"SMP_forecast_(\d{4}-\d{2}-\d{2})_issue(\d{4})\.csv$")


def _num(value: float | int | None, nd: int = 4) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def _money(value: float | int | None) -> int | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(round(f))


def _score_map(path: Path | None) -> dict[tuple[str, str, str], float]:
    if not path or not path.exists():
        return {}
    scored = pd.read_csv(path)
    out: dict[tuple[str, str, str], float] = {}
    if scored.empty:
        return out
    for row in scored.itertuples(index=False):
        mape = getattr(row, "mape", None)
        try:
            if pd.notna(mape):
                out[(str(row.target_date), str(row.region), str(row.model_id))] = float(mape)
        except AttributeError:
            continue
    return out


def _row(
    *,
    target_date: str,
    region: str,
    source: str,
    model_id: str,
    model_name: str,
    issue_hour: str | None,
    prices: list[float],
    mape_pct: float,
) -> dict:
    inputs = RevenueInputs(
        region=region,
        target_date=date.fromisoformat(target_date),
        mape_pct=mape_pct,
    )
    result = estimate_revenue(prices, inputs)
    outputs = result["outputs"]
    ec = outputs["effective_capacity"]
    return {
        "target_date": target_date,
        "region": region,
        "source": source,
        "issue_hour": issue_hour,
        "model_id": model_id,
        "model_name": model_name,
        "n_hours": len(prices),
        "avg_smp": _num(outputs["prices"]["avg_krw_per_kwh"], 4),
        "low_smp": _num(outputs["prices"]["low_krw_per_kwh"], 4),
        "high_smp": _num(outputs["prices"]["high_krw_per_kwh"], 4),
        "spread_smp": _num(outputs["prices"]["spread_krw_per_kwh"], 4),
        "mape_pct": _num(mape_pct, 4),
        "pv_effective_mw": _num(ec["pv_effective_mw"], 4),
        "ess_effective_mw": _num(ec["ess_effective_mw"], 4),
        "market_revenue_krw": _money(outputs["market_revenue_krw"]),
        "ess_revenue_krw": _money(outputs["ess_revenue_krw"]),
        "capacity_revenue_krw": _money(outputs["capacity_revenue_krw"]),
        "subsidy_revenue_krw": _money(outputs["subsidy_revenue_krw"]),
        "imbalance_penalty_krw": _money(outputs["imbalance_penalty_krw"]),
        "total_revenue_krw": _money(outputs["total_revenue_krw"]),
    }


def forecast_rows(forecast_dir: Path, score_history: Path | None) -> list[dict]:
    score = _score_map(score_history)
    rows: list[dict] = []
    for path in sorted(forecast_dir.glob("SMP_forecast_*_issue*.csv")):
        match = FORECAST_RE.match(path.name)
        if not match:
            continue
        target_date, issue_hour = match.groups()
        fc = pd.read_csv(path, parse_dates=["target_date"])
        required = {"region", "target_date", "hour_end", "model_id", "model_name", "p50"}
        missing = required - set(fc.columns)
        if missing:
            raise ValueError(f"{path} missing columns: {sorted(missing)}")
        fc["target_date"] = fc["target_date"].dt.date.astype(str)
        fc["hour_end"] = pd.to_numeric(fc["hour_end"], errors="coerce")
        fc["p50"] = pd.to_numeric(fc["p50"], errors="coerce")
        fc = fc.dropna(subset=["region", "target_date", "hour_end", "model_id", "p50"])
        for (region, model_id, model_name), group in fc.groupby(["region", "model_id", "model_name"]):
            ordered = group.sort_values("hour_end")
            prices = [float(v) for v in ordered["p50"].tolist() if pd.notna(v)]
            if not prices:
                continue
            mape = score.get((target_date, str(region), str(model_id)), 8.0)
            rows.append(_row(
                target_date=target_date,
                region=str(region),
                source="forecast",
                model_id=str(model_id),
                model_name=str(model_name),
                issue_hour=issue_hour,
                prices=prices,
                mape_pct=mape,
            ))
    return rows


def actual_rows(actuals_path: Path) -> list[dict]:
    if not actuals_path.exists():
        return []
    actuals = pd.read_csv(actuals_path, parse_dates=["target_date"])
    required = {"region", "target_date", "hour_end", "smp"}
    missing = required - set(actuals.columns)
    if missing:
        raise ValueError(f"{actuals_path} missing columns: {sorted(missing)}")
    actuals["target_date"] = actuals["target_date"].dt.date.astype(str)
    actuals["hour_end"] = pd.to_numeric(actuals["hour_end"], errors="coerce")
    actuals["smp"] = pd.to_numeric(actuals["smp"], errors="coerce")
    actuals = actuals.dropna(subset=["region", "target_date", "hour_end", "smp"])
    rows: list[dict] = []
    for (target_date, region), group in actuals.groupby(["target_date", "region"]):
        ordered = group.sort_values("hour_end")
        prices = [float(v) for v in ordered["smp"].tolist() if pd.notna(v)]
        if len(prices) < 24:
            continue
        rows.append(_row(
            target_date=str(target_date),
            region=str(region),
            source="actual",
            model_id="ACTUAL",
            model_name="실측 SMP",
            issue_hour=None,
            prices=prices[:24],
            mape_pct=0.0,
        ))
    return rows


def build(forecast_dir: Path, actuals: Path, out: Path, score_history: Path | None) -> pd.DataFrame:
    rows = [*actual_rows(actuals), *forecast_rows(forecast_dir, score_history)]
    columns = [
        "target_date", "region", "source", "issue_hour", "model_id", "model_name",
        "n_hours", "avg_smp", "low_smp", "high_smp", "spread_smp", "mape_pct",
        "pv_effective_mw", "ess_effective_mw", "market_revenue_krw",
        "ess_revenue_krw", "capacity_revenue_krw", "subsidy_revenue_krw",
        "imbalance_penalty_krw", "total_revenue_krw",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame = frame.drop_duplicates(
            ["target_date", "region", "source", "model_id"], keep="last"
        )
        frame["_date"] = pd.to_datetime(frame["target_date"], errors="coerce")
        frame = frame.sort_values(
            ["_date", "region", "source", "model_id"],
            ascending=[False, True, True, True],
        ).drop(columns=["_date"]).reset_index(drop=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False, encoding="utf-8-sig")
    return frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ESS/VPP revenue history")
    parser.add_argument("--forecast-dir", type=Path, required=True)
    parser.add_argument("--actuals", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--score-history", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = build(args.forecast_dir, args.actuals, args.out, args.score_history)
    dates = frame["target_date"].nunique() if not frame.empty else 0
    print(f"wrote {args.out} | rows={len(frame)} | dates={dates}")


if __name__ == "__main__":
    main()
