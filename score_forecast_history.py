#!/usr/bin/env python3
"""Build a cumulative daily forecast-vs-actual score history.

The daily agent writes one forecast CSV per target date before actual SMP is
known. This script revisits those CSVs after EPSIS actuals have landed in the
cache, scores every available region/model/date, and writes a de-duplicated
history sorted by nearest target date first.
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import pandas as pd


FORECAST_RE = re.compile(r"SMP_forecast_(\d{4}-\d{2}-\d{2})_issue(\d{4})\.csv$")


def _num(v: float | int | None, nd: int = 4) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def find_forecast_files(forecast_dir: Path) -> list[Path]:
    files = []
    for path in forecast_dir.glob("SMP_forecast_*_issue*.csv"):
        if FORECAST_RE.match(path.name):
            files.append(path)
    return sorted(files)


def load_actuals(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"actuals cache not found: {path}")
    actuals = pd.read_csv(path, parse_dates=["target_date"])
    required = {"region", "target_date", "hour_end", "smp"}
    missing = required - set(actuals.columns)
    if missing:
        raise ValueError(f"actuals cache missing columns: {sorted(missing)}")
    actuals = actuals[list(required)].copy()
    actuals["target_date"] = actuals["target_date"].dt.date.astype(str)
    actuals["hour_end"] = pd.to_numeric(actuals["hour_end"], errors="coerce").astype("Int64")
    actuals["smp"] = pd.to_numeric(actuals["smp"], errors="coerce")
    actuals = actuals.dropna(subset=["region", "target_date", "hour_end", "smp"])
    return actuals.rename(columns={"smp": "actual_smp"})


def score_file(path: Path, actuals: pd.DataFrame) -> pd.DataFrame:
    m = FORECAST_RE.match(path.name)
    if not m:
        return pd.DataFrame()
    issue_hour = m.group(2)

    fc = pd.read_csv(path, parse_dates=["target_date"])
    required = {"region", "target_date", "hour_end", "model_id", "model_name", "p50"}
    missing = required - set(fc.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")

    keep = ["region", "target_date", "hour_end", "model_id", "model_name", "p50"]
    if "model_ko" in fc.columns:
        keep.append("model_ko")
    fc = fc[keep].copy()
    fc["target_date"] = fc["target_date"].dt.date.astype(str)
    fc["hour_end"] = pd.to_numeric(fc["hour_end"], errors="coerce").astype("Int64")
    fc["p50"] = pd.to_numeric(fc["p50"], errors="coerce")
    fc = fc.dropna(subset=["region", "target_date", "hour_end", "model_id", "p50"])

    merged = fc.merge(actuals, on=["region", "target_date", "hour_end"], how="inner")
    if merged.empty:
        return pd.DataFrame()

    rows = []
    group_cols = ["target_date", "region", "model_id", "model_name"]
    if "model_ko" in merged.columns:
        group_cols.append("model_ko")

    for keys, g in merged.groupby(group_cols, dropna=False):
        data = dict(zip(group_cols, keys if isinstance(keys, tuple) else (keys,)))
        pairs = g[["actual_smp", "p50"]].dropna()
        if pairs.empty:
            continue
        err = pairs["p50"] - pairs["actual_smp"]
        abs_err = err.abs()
        mape_base = pairs[pairs["actual_smp"].abs() > 1.0]
        mape = None
        if not mape_base.empty:
            mape = ((mape_base["p50"] - mape_base["actual_smp"]).abs() / mape_base["actual_smp"].abs()).mean() * 100
        smape = (2 * abs_err / (pairs["actual_smp"].abs() + pairs["p50"].abs()).clip(lower=1e-6)).mean() * 100
        rmse = math.sqrt((err ** 2).mean())
        score = None if mape is None else max(0.0, 100.0 - float(mape))

        rows.append({
            "target_date": data["target_date"],
            "region": data["region"],
            "issue_hour": issue_hour,
            "model_id": data["model_id"],
            "model_name": data["model_name"],
            "model_ko": data.get("model_ko") or data["model_name"],
            "n_hours": int(len(pairs)),
            "actual_avg": _num(pairs["actual_smp"].mean(), 4),
            "forecast_avg": _num(pairs["p50"].mean(), 4),
            "bias": _num(err.mean(), 4),
            "mae": _num(abs_err.mean(), 4),
            "rmse": _num(rmse, 4),
            "mape": _num(mape, 4),
            "smape": _num(smape, 4),
            "score": _num(score, 4),
            "forecast_file": path.name,
        })

    return pd.DataFrame(rows)


def build_history(forecast_dir: Path, actuals_path: Path, out_path: Path) -> pd.DataFrame:
    actuals = load_actuals(actuals_path)
    frames = [score_file(path, actuals) for path in find_forecast_files(forecast_dir)]
    nonempty = [f for f in frames if not f.empty]
    scored = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()

    if out_path.exists():
        existing = pd.read_csv(out_path)
        scored = pd.concat([existing, scored], ignore_index=True) if not scored.empty else existing

    columns = [
        "target_date", "region", "issue_hour", "model_id", "model_name", "model_ko",
        "n_hours", "actual_avg", "forecast_avg", "bias", "mae", "rmse", "mape",
        "smape", "score", "forecast_file",
    ]
    if scored.empty:
        scored = pd.DataFrame(columns=columns)
    else:
        scored = scored[columns].drop_duplicates(
            ["target_date", "region", "issue_hour", "model_id"], keep="last"
        )
        scored["_date"] = pd.to_datetime(scored["target_date"], errors="coerce")
        scored["_score_sort"] = pd.to_numeric(scored["score"], errors="coerce").fillna(-1)
        scored = scored.sort_values(
            ["_date", "region", "_score_sort", "model_id"],
            ascending=[False, True, False, True],
        ).drop(columns=["_date", "_score_sort"]).reset_index(drop=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(out_path, index=False, encoding="utf-8-sig")
    return scored


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score daily SMP forecasts against EPSIS actuals")
    parser.add_argument("--forecast-dir", type=Path, required=True)
    parser.add_argument("--actuals", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scored = build_history(args.forecast_dir, args.actuals, args.out)
    dates = scored["target_date"].nunique() if not scored.empty else 0
    print(f"wrote {args.out} | rows={len(scored)} | scored_dates={dates}")


if __name__ == "__main__":
    main()
