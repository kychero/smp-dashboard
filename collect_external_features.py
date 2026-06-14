#!/home/opc/smp/venv/bin/python
"""Collect design-spec external features for SMP models.

Output schema is consumed by daily_smp_agent.py via external_features_path.

This script intentionally keeps operational dependencies low:
  - Weather features come from Open-Meteo forecast API (no API key).
  - PV generation is estimated as an irradiance-based proxy unless a real
    source CSV is supplied.
  - Demand and measured PV can be merged from CSVs when KPX/API exports are
    available.

Required output keys:
  region,target_date,hour_end
Optional model inputs:
  demand_forecast_d1,pv_forecast_total,wind_forecast_total,temp_pop_weighted,
  irradiance_avg,wind_speed_avg,...
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd


REGION_POINTS = {
    "LAND": [
        # name, lat, lon, rough population/system relevance weight
        ("Seoul", 37.5665, 126.9780, 0.42),
        ("Daejeon", 36.3504, 127.3845, 0.18),
        ("Daegu", 35.8714, 128.6014, 0.14),
        ("Busan", 35.1796, 129.0756, 0.16),
        ("Gwangju", 35.1595, 126.8526, 0.10),
    ],
    "JEJU": [
        ("Jeju", 33.4996, 126.5312, 0.70),
        ("Seogwipo", 33.2541, 126.5601, 0.30),
    ],
}

DEFAULT_PV_CAPACITY_MW = {
    # Rough proxy scaling only. Replace with real DS-06/M2 output when available.
    "LAND": 25_000.0,
    "JEJU": 1_100.0,
}

EPSIS_REAL_DEMAND_URL = "https://epsis.kpx.or.kr/epsisnew/selectEkgeEpsMepRealGridAjax.ajax"

KR_PUBLIC_HOLIDAYS = {
    # 2023
    "2023-01-01", "2023-01-21", "2023-01-22", "2023-01-23", "2023-01-24",
    "2023-03-01", "2023-05-05", "2023-05-27", "2023-05-29", "2023-06-06",
    "2023-08-15", "2023-09-28", "2023-09-29", "2023-09-30", "2023-10-02",
    "2023-10-03", "2023-10-09", "2023-12-25",
    # 2024
    "2024-01-01", "2024-02-09", "2024-02-10", "2024-02-11", "2024-02-12",
    "2024-03-01", "2024-04-10", "2024-05-05", "2024-05-06", "2024-05-15",
    "2024-06-06", "2024-08-15", "2024-09-16", "2024-09-17", "2024-09-18",
    "2024-10-03", "2024-10-09", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-03-01",
    "2025-03-03", "2025-05-05", "2025-05-06", "2025-06-03", "2025-06-06",
    "2025-08-15", "2025-10-03", "2025-10-05", "2025-10-06", "2025-10-07",
    "2025-10-08", "2025-10-09", "2025-12-25",
    # 2026
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-01",
    "2026-03-02", "2026-05-05", "2026-05-24", "2026-05-25", "2026-06-03",
    "2026-06-06", "2026-08-15", "2026-08-17", "2026-09-24", "2026-09-25",
    "2026-09-26", "2026-10-03", "2026-10-05", "2026-10-09", "2026-12-25",
}


def _request_open_meteo(base_url: str, lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,shortwave_radiation,wind_speed_10m",
        "timezone": "Asia/Seoul",
        "start_date": start,
        "end_date": end,
    }
    url = base_url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    hourly = payload.get("hourly") or {}
    if not hourly.get("time"):
        raise RuntimeError(f"Open-Meteo returned no hourly data for {lat},{lon}: {payload}")
    return pd.DataFrame(
        {
            "ts": pd.to_datetime(hourly["time"]),
            "temp": pd.to_numeric(hourly.get("temperature_2m"), errors="coerce"),
            "irradiance": pd.to_numeric(hourly.get("shortwave_radiation"), errors="coerce"),
            "wind_speed": pd.to_numeric(hourly.get("wind_speed_10m"), errors="coerce"),
        }
    )


def fetch_open_meteo(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    today = dt.date.today()
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    frames = []
    if start_d < today:
        hist_end = min(end_d, today - dt.timedelta(days=1))
        frames.append(
            _request_open_meteo(
                "https://archive-api.open-meteo.com/v1/archive",
                lat,
                lon,
                start_d.isoformat(),
                hist_end.isoformat(),
            )
        )
    if end_d >= today:
        fut_start = max(start_d, today)
        frames.append(
            _request_open_meteo(
                "https://api.open-meteo.com/v1/forecast",
                lat,
                lon,
                fut_start.isoformat(),
                end_d.isoformat(),
            )
        )
    if not frames:
        raise RuntimeError(f"No Open-Meteo request range for {start}~{end}")
    return pd.concat(frames, ignore_index=True).drop_duplicates("ts", keep="last")


def weather_by_region(start: str, end: str) -> pd.DataFrame:
    frames = []
    for region, points in REGION_POINTS.items():
        acc = None
        for _, lat, lon, weight in points:
            df = fetch_open_meteo(lat, lon, start, end)
            val = df[["ts"]].copy()
            val["temp_pop_weighted"] = df["temp"] * weight
            val["irradiance_avg"] = df["irradiance"] * weight
            val["wind_speed_avg"] = df["wind_speed"] * weight
            acc = val if acc is None else acc.merge(val, on="ts", how="outer", suffixes=("", "_next"))
            if "temp_pop_weighted_next" in acc:
                for col in ["temp_pop_weighted", "irradiance_avg", "wind_speed_avg"]:
                    acc[col] = acc[col].fillna(0) + acc.pop(f"{col}_next").fillna(0)
        acc["region"] = region
        frames.append(acc)
    out = pd.concat(frames, ignore_index=True)
    out["target_date"] = out["ts"].dt.normalize()
    out["hour_end"] = out["ts"].dt.hour + 1
    out.loc[out["hour_end"] == 25, "hour_end"] = 24
    out["target_date"] = out["target_date"].dt.date.astype(str)
    return out[["region", "target_date", "hour_end", "temp_pop_weighted", "irradiance_avg", "wind_speed_avg"]]


def add_pv_proxy(df: pd.DataFrame, capacities: dict[str, float]) -> pd.DataFrame:
    out = df.copy()
    # Convert W/m2 to a conservative capacity factor. This is a proxy for testing,
    # not a replacement for DS-06/M2 generation forecast.
    cf = (out["irradiance_avg"].clip(lower=0) / 1000.0 * 0.82).clip(upper=0.90)
    out["pv_forecast_total"] = [
        round(float(cf_i) * capacities.get(region, 0.0), 3)
        for cf_i, region in zip(cf, out["region"])
    ]
    return out


def _clean_number(value: object) -> float:
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return math.nan
    return float(text)


def _date_chunks(start: dt.date, end: dt.date, days: int = 7) -> list[tuple[dt.date, dt.date]]:
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(end, cur + dt.timedelta(days=days - 1))
        chunks.append((cur, chunk_end))
        cur = chunk_end + dt.timedelta(days=1)
    return chunks


def fetch_epsis_real_demand(start: str, end: str, regions: list[str]) -> pd.DataFrame:
    """Fetch actual 5-minute KPX/EPSIS load and aggregate it to hourly demand.

    EPSIS exposes current/actual load, not a day-ahead forecast. The returned
    system load is duplicated by region because the public page does not expose
    a LAND/JEJU split.
    """
    start_d = dt.date.fromisoformat(start)
    end_d = min(dt.date.fromisoformat(end), dt.date.today())
    if start_d > end_d:
        return pd.DataFrame(columns=["region", "target_date", "hour_end", "demand_forecast_d1"])

    rows = []
    pattern = re.compile(
        r'c2\s*=\s*textFormmat\("(?P<load>[^"]*)",0\);.*?'
        r'gridData\.push\(\{"year":"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2})"',
        re.S,
    )
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://epsis.kpx.or.kr/epsisnew/selectEkgeEpsMepRealChart.do?menuId=030300",
        "User-Agent": "Mozilla/5.0",
    }
    for chunk_start, chunk_end in _date_chunks(start_d, end_d):
        form = urllib.parse.urlencode(
            {
                "beginDate": chunk_start.strftime("%Y%m%d"),
                "endDate": chunk_end.strftime("%Y%m%d"),
                "editYn": "N",
            }
        ).encode("utf-8")
        req = urllib.request.Request(EPSIS_REAL_DEMAND_URL, data=form, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        for match in pattern.finditer(text):
            ts = pd.Timestamp(match.group("ts"))
            rows.append({"ts": ts, "load": _clean_number(match.group("load"))})

    if not rows:
        return pd.DataFrame(columns=["region", "target_date", "hour_end", "demand_forecast_d1"])

    raw = pd.DataFrame(rows).dropna(subset=["load"]).drop_duplicates("ts", keep="last")
    raw["target_date"] = raw["ts"].dt.date.astype(str)
    raw["hour_end"] = raw["ts"].dt.hour + 1
    hourly = (
        raw.groupby(["target_date", "hour_end"], as_index=False)["load"]
        .mean()
        .rename(columns={"load": "demand_forecast_d1"})
    )
    return pd.concat(
        [hourly.assign(region=region) for region in regions],
        ignore_index=True,
    )[["region", "target_date", "hour_end", "demand_forecast_d1"]]


def fill_demand_proxy_from_history(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "demand_forecast_d1" not in out.columns:
        return out

    out["target_date"] = pd.to_datetime(out["target_date"]).dt.date.astype(str)
    out["hour_end"] = pd.to_numeric(out["hour_end"], errors="raise").astype(int)
    out["_date"] = pd.to_datetime(out["target_date"])
    out["_dow"] = out["_date"].dt.dayofweek
    out["_month"] = out["_date"].dt.month
    out["_season"] = (out["_month"] % 12) // 3
    out["_is_weekend"] = out["_dow"].isin([5, 6])
    out["_is_holiday"] = out["target_date"].isin(KR_PUBLIC_HOLIDAYS)
    out["_day_type"] = "weekday"
    out.loc[out["_is_weekend"], "_day_type"] = "weekend"
    out.loc[out["_is_holiday"], "_day_type"] = "holiday"

    hist = out[out["demand_forecast_d1"].notna()].copy()
    if hist.empty:
        out.drop(columns=["_date", "_dow", "_month", "_season", "_is_weekend", "_is_holiday", "_day_type"], inplace=True)
        return out

    missing_idx = out.index[out["demand_forecast_d1"].isna()].tolist()
    for idx in missing_idx:
        row = out.loc[idx]
        candidates = hist[
            (hist["region"] == row["region"])
            & (hist["hour_end"] == row["hour_end"])
            & (hist["_date"] < row["_date"])
        ].copy()
        if candidates.empty:
            continue

        days_ago = (row["_date"] - candidates["_date"]).dt.days.clip(lower=1)
        season_distance = (candidates["_season"] - row["_season"]).abs()
        season_distance = season_distance.where(season_distance <= 2, 4 - season_distance)
        month_distance = (candidates["_month"] - row["_month"]).abs()
        month_distance = month_distance.where(month_distance <= 6, 12 - month_distance)

        weights = 1.0 / (1.0 + days_ago / 45.0)
        weights *= 1.0 / (1.0 + season_distance)
        weights *= 1.0 / (1.0 + month_distance / 2.0)
        weights *= candidates["_day_type"].eq(row["_day_type"]).map({True: 4.0, False: 0.35})
        weights *= candidates["_dow"].eq(row["_dow"]).map({True: 1.6, False: 1.0})

        if "temp_pop_weighted" in candidates.columns and pd.notna(row.get("temp_pop_weighted")):
            temp_diff = (candidates["temp_pop_weighted"] - row["temp_pop_weighted"]).abs()
            weights *= 1.0 / (1.0 + temp_diff.fillna(temp_diff.median()) / 5.0)

        strongest = candidates.assign(_weight=weights).nlargest(90, "_weight")
        weight_sum = strongest["_weight"].sum()
        if weight_sum > 0:
            out.at[idx, "demand_forecast_d1"] = float(
                (strongest["demand_forecast_d1"] * strongest["_weight"]).sum() / weight_sum
            )

    hour_avg = hist.groupby(["region", "hour_end"], as_index=False)["demand_forecast_d1"].mean()
    hour_avg.rename(columns={"demand_forecast_d1": "_hour_demand"}, inplace=True)
    out = out.merge(hour_avg, on=["region", "hour_end"], how="left")
    out["demand_forecast_d1"] = out["demand_forecast_d1"].combine_first(out["_hour_demand"])

    out.drop(
        columns=["_date", "_dow", "_month", "_season", "_is_weekend", "_is_holiday", "_day_type", "_hour_demand"],
        inplace=True,
    )
    return out


def merge_optional_csv(base: pd.DataFrame, path: Path | None, columns: list[str]) -> pd.DataFrame:
    if not path:
        return base
    if not path.exists():
        raise FileNotFoundError(path)
    src = pd.read_csv(path)
    required = {"region", "target_date", "hour_end"}
    missing = required - set(src.columns)
    if missing:
        raise ValueError(f"{path} missing required columns: {sorted(missing)}")
    keep = ["region", "target_date", "hour_end"] + [c for c in columns if c in src.columns]
    src = src[keep].copy()
    src["target_date"] = pd.to_datetime(src["target_date"]).dt.date.astype(str)
    src["hour_end"] = pd.to_numeric(src["hour_end"], errors="raise").astype(int)
    return base.merge(src, on=["region", "target_date", "hour_end"], how="left", suffixes=("", "_src"))


def coalesce_source_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["demand_forecast_d1", "pv_forecast_total", "wind_forecast_total"]:
        src = f"{col}_src"
        if src in out.columns:
            out[col] = out[src].combine_first(out.get(col))
            out.drop(columns=[src], inplace=True)
    return out


def build(args: argparse.Namespace) -> pd.DataFrame:
    df = weather_by_region(args.start_date, args.end_date)
    if args.pv_proxy:
        capacities = dict(DEFAULT_PV_CAPACITY_MW)
        if args.land_pv_capacity_mw is not None:
            capacities["LAND"] = args.land_pv_capacity_mw
        if args.jeju_pv_capacity_mw is not None:
            capacities["JEJU"] = args.jeju_pv_capacity_mw
        df = add_pv_proxy(df, capacities)
    if args.epsis_demand:
        epsis_demand = fetch_epsis_real_demand(args.start_date, args.end_date, list(REGION_POINTS))
        if not epsis_demand.empty:
            df = df.merge(epsis_demand, on=["region", "target_date", "hour_end"], how="left")
    df = merge_optional_csv(df, args.demand_csv, ["demand_forecast_d1"])
    df = merge_optional_csv(df, args.pv_csv, ["pv_forecast_total", "wind_forecast_total"])
    df = coalesce_source_columns(df)
    for col in ["demand_forecast_d1", "wind_forecast_total"]:
        if col not in df.columns:
            df[col] = math.nan
    order = [
        "region",
        "target_date",
        "hour_end",
        "demand_forecast_d1",
        "pv_forecast_total",
        "wind_forecast_total",
        "temp_pop_weighted",
        "irradiance_avg",
        "wind_speed_avg",
    ]
    return df[order].sort_values(["region", "target_date", "hour_end"]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", type=Path, default=Path("/home/opc/smp/data/external_features.csv"))
    ap.add_argument("--demand-csv", type=Path, default=None,
                    help="Optional CSV with region,target_date,hour_end,demand_forecast_d1")
    ap.add_argument("--pv-csv", type=Path, default=None,
                    help="Optional CSV with region,target_date,hour_end,pv_forecast_total[,wind_forecast_total]")
    ap.add_argument("--pv-proxy", action="store_true",
                    help="Fill pv_forecast_total from irradiance proxy when no real PV source is available")
    ap.add_argument("--epsis-demand", action="store_true",
                    help="Fetch KPX/EPSIS actual load and aggregate to hourly demand input")
    ap.add_argument("--land-pv-capacity-mw", type=float, default=None)
    ap.add_argument("--jeju-pv-capacity-mw", type=float, default=None)
    ap.add_argument("--merge-existing", action="store_true",
                    help="Merge generated rows into --out instead of replacing the file")
    args = ap.parse_args()

    out = build(args)
    if args.merge_existing and args.out.exists():
        existing = pd.read_csv(args.out)
        existing["target_date"] = pd.to_datetime(existing["target_date"]).dt.date.astype(str)
        existing["hour_end"] = pd.to_numeric(existing["hour_end"], errors="raise").astype(int)
        out = (
            pd.concat([existing, out], ignore_index=True)
            .drop_duplicates(["region", "target_date", "hour_end"], keep="last")
            .sort_values(["region", "target_date", "hour_end"])
            .reset_index(drop=True)
        )
    if args.epsis_demand:
        out = fill_demand_proxy_from_history(out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    non_null = {c: int(out[c].notna().sum()) for c in out.columns if c not in {"region", "target_date", "hour_end"}}
    print(json.dumps({"out": str(args.out), "rows": len(out), "non_null": non_null}, ensure_ascii=False))


if __name__ == "__main__":
    main()
