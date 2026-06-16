#!/usr/bin/env python3
"""Load existing SMP CSV artifacts into the Phase 2 PostgreSQL schema."""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import pandas as pd

from . import db


ROOT = Path(__file__).resolve().parents[1]
FORECAST_RE = re.compile(r"SMP_forecast_\d{4}-\d{2}-\d{2}_issue\d{4}\.csv$")


def _none_if_nan(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def load_actuals(path: Path) -> int:
    if not path.exists():
        return 0
    frame = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["target_date", "target_ts_end"])
    rows = [
        {
            "region": row.region,
            "target_date": row.target_date.date(),
            "hour_end": int(row.hour_end),
            "ts_end": _none_if_nan(row.target_ts_end),
            "smp": float(row.smp),
        }
        for row in frame.itertuples(index=False)
        if _none_if_nan(row.smp) is not None
    ]
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vpp.smp_actual (region, target_date, hour_end, ts_end, smp)
                VALUES (%(region)s, %(target_date)s, %(hour_end)s, %(ts_end)s, %(smp)s)
                ON CONFLICT (region, target_date, hour_end) DO UPDATE
                SET ts_end = EXCLUDED.ts_end,
                    smp = EXCLUDED.smp,
                    loaded_at = now()
                """,
                rows,
            )
    return len(rows)


def load_forecasts(path: Path) -> int:
    if not path.exists():
        return 0
    frame = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["target_date", "issue_ts_kst"])
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            {
                "region": row.region,
                "target_date": row.target_date.date(),
                "hour_end": int(row.hour_end),
                "model_id": row.model_id,
                "model_name": _none_if_nan(row.model_name),
                "p10": _none_if_nan(row.p10),
                "p25": _none_if_nan(row.p25),
                "p50": _none_if_nan(row.p50),
                "p75": _none_if_nan(row.p75),
                "p90": _none_if_nan(row.p90),
                "unit": getattr(row, "unit", "KRW/kWh") or "KRW/kWh",
                "issue_ts_kst": _none_if_nan(row.issue_ts_kst),
                "forecast_file": path.name,
            }
        )
    if not rows:
        return 0
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vpp.smp_forecast (
                    region, target_date, hour_end, model_id, model_name,
                    p10, p25, p50, p75, p90, unit, issue_ts_kst, forecast_file
                )
                VALUES (
                    %(region)s, %(target_date)s, %(hour_end)s, %(model_id)s, %(model_name)s,
                    %(p10)s, %(p25)s, %(p50)s, %(p75)s, %(p90)s, %(unit)s,
                    %(issue_ts_kst)s, %(forecast_file)s
                )
                ON CONFLICT (region, target_date, hour_end, model_id) DO UPDATE
                SET model_name = EXCLUDED.model_name,
                    p10 = EXCLUDED.p10,
                    p25 = EXCLUDED.p25,
                    p50 = EXCLUDED.p50,
                    p75 = EXCLUDED.p75,
                    p90 = EXCLUDED.p90,
                    unit = EXCLUDED.unit,
                    issue_ts_kst = EXCLUDED.issue_ts_kst,
                    forecast_file = EXCLUDED.forecast_file,
                    loaded_at = now()
                """,
                rows,
            )
    return len(rows)


def load_forecast_dir(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for forecast in sorted(path.glob("SMP_forecast_*_issue*.csv")):
        if FORECAST_RE.match(forecast.name):
            total += load_forecasts(forecast)
    return total


def load_scores(path: Path) -> int:
    if not path.exists():
        return 0
    frame = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["target_date"])
    if frame.empty:
        return 0
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            {
                "target_date": row.target_date.date(),
                "region": row.region,
                "issue_hour": _none_if_nan(row.issue_hour),
                "model_id": row.model_id,
                "model_name": _none_if_nan(row.model_name),
                "model_ko": _none_if_nan(row.model_ko),
                "n_hours": _none_if_nan(row.n_hours),
                "actual_avg": _none_if_nan(row.actual_avg),
                "forecast_avg": _none_if_nan(row.forecast_avg),
                "bias": _none_if_nan(row.bias),
                "mae": _none_if_nan(row.mae),
                "rmse": _none_if_nan(row.rmse),
                "mape": _none_if_nan(row.mape),
                "smape": _none_if_nan(row.smape),
                "score": _none_if_nan(row.score),
                "forecast_file": _none_if_nan(row.forecast_file),
            }
        )
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vpp.forecast_score (
                    target_date, region, issue_hour, model_id, model_name, model_ko,
                    n_hours, actual_avg, forecast_avg, bias, mae, rmse, mape, smape,
                    score, forecast_file
                )
                VALUES (
                    %(target_date)s, %(region)s, %(issue_hour)s, %(model_id)s,
                    %(model_name)s, %(model_ko)s, %(n_hours)s, %(actual_avg)s,
                    %(forecast_avg)s, %(bias)s, %(mae)s, %(rmse)s, %(mape)s,
                    %(smape)s, %(score)s, %(forecast_file)s
                )
                ON CONFLICT (target_date, region, model_id) DO UPDATE
                SET issue_hour = EXCLUDED.issue_hour,
                    model_name = EXCLUDED.model_name,
                    model_ko = EXCLUDED.model_ko,
                    n_hours = EXCLUDED.n_hours,
                    actual_avg = EXCLUDED.actual_avg,
                    forecast_avg = EXCLUDED.forecast_avg,
                    bias = EXCLUDED.bias,
                    mae = EXCLUDED.mae,
                    rmse = EXCLUDED.rmse,
                    mape = EXCLUDED.mape,
                    smape = EXCLUDED.smape,
                    score = EXCLUDED.score,
                    forecast_file = EXCLUDED.forecast_file,
                    loaded_at = now()
                """,
                rows,
            )
    return len(rows)


def load_revenue_history(path: Path) -> int:
    if not path.exists():
        return 0
    frame = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["target_date"])
    if frame.empty:
        return 0
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            {
                "target_date": row.target_date.date(),
                "region": row.region,
                "source": row.source,
                "issue_hour": _none_if_nan(row.issue_hour),
                "model_id": row.model_id,
                "model_name": _none_if_nan(row.model_name),
                "n_hours": _none_if_nan(row.n_hours),
                "avg_smp": _none_if_nan(row.avg_smp),
                "low_smp": _none_if_nan(row.low_smp),
                "high_smp": _none_if_nan(row.high_smp),
                "spread_smp": _none_if_nan(row.spread_smp),
                "mape_pct": _none_if_nan(row.mape_pct),
                "pv_effective_mw": _none_if_nan(row.pv_effective_mw),
                "ess_effective_mw": _none_if_nan(row.ess_effective_mw),
                "market_revenue_krw": _none_if_nan(row.market_revenue_krw),
                "ess_revenue_krw": _none_if_nan(row.ess_revenue_krw),
                "capacity_revenue_krw": _none_if_nan(row.capacity_revenue_krw),
                "subsidy_revenue_krw": _none_if_nan(row.subsidy_revenue_krw),
                "imbalance_penalty_krw": _none_if_nan(row.imbalance_penalty_krw),
                "total_revenue_krw": _none_if_nan(row.total_revenue_krw),
            }
        )
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vpp.revenue_history (
                    target_date, region, source, issue_hour, model_id, model_name,
                    n_hours, avg_smp, low_smp, high_smp, spread_smp, mape_pct,
                    pv_effective_mw, ess_effective_mw, market_revenue_krw,
                    ess_revenue_krw, capacity_revenue_krw, subsidy_revenue_krw,
                    imbalance_penalty_krw, total_revenue_krw
                )
                VALUES (
                    %(target_date)s, %(region)s, %(source)s, %(issue_hour)s,
                    %(model_id)s, %(model_name)s, %(n_hours)s, %(avg_smp)s,
                    %(low_smp)s, %(high_smp)s, %(spread_smp)s, %(mape_pct)s,
                    %(pv_effective_mw)s, %(ess_effective_mw)s,
                    %(market_revenue_krw)s, %(ess_revenue_krw)s,
                    %(capacity_revenue_krw)s, %(subsidy_revenue_krw)s,
                    %(imbalance_penalty_krw)s, %(total_revenue_krw)s
                )
                ON CONFLICT (target_date, region, source, model_id) DO UPDATE
                SET issue_hour = EXCLUDED.issue_hour,
                    model_name = EXCLUDED.model_name,
                    n_hours = EXCLUDED.n_hours,
                    avg_smp = EXCLUDED.avg_smp,
                    low_smp = EXCLUDED.low_smp,
                    high_smp = EXCLUDED.high_smp,
                    spread_smp = EXCLUDED.spread_smp,
                    mape_pct = EXCLUDED.mape_pct,
                    pv_effective_mw = EXCLUDED.pv_effective_mw,
                    ess_effective_mw = EXCLUDED.ess_effective_mw,
                    market_revenue_krw = EXCLUDED.market_revenue_krw,
                    ess_revenue_krw = EXCLUDED.ess_revenue_krw,
                    capacity_revenue_krw = EXCLUDED.capacity_revenue_krw,
                    subsidy_revenue_krw = EXCLUDED.subsidy_revenue_krw,
                    imbalance_penalty_krw = EXCLUDED.imbalance_penalty_krw,
                    total_revenue_krw = EXCLUDED.total_revenue_krw,
                    loaded_at = now()
                """,
                rows,
            )
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest SMP CSV artifacts into PostgreSQL")
    parser.add_argument("--actuals", type=Path, default=ROOT / "processed/smp_actuals_cache.csv")
    parser.add_argument("--forecast", type=Path, default=None)
    parser.add_argument("--forecast-dir", type=Path, default=Path("/home/opc/smp/data/daily_outputs"))
    parser.add_argument("--scores", type=Path, default=Path("/home/opc/smp/data/forecast_score_history.csv"))
    parser.add_argument("--revenue-history", type=Path, default=Path("/home/opc/smp/data/revenue_history.csv"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    forecast_count = load_forecasts(args.forecast) if args.forecast else load_forecast_dir(args.forecast_dir)
    counts = {
        "actuals": load_actuals(args.actuals),
        "forecasts": forecast_count,
        "scores": load_scores(args.scores),
        "revenue_history": load_revenue_history(args.revenue_history),
    }
    print(counts)


if __name__ == "__main__":
    main()
