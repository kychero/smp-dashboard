#!/usr/bin/env python3
"""SMP 예측 툴 산출물(xlsx) -> 대시보드용 data.js 생성.

입력:
  - 예측 엑셀: all_model_forecasts / summary 시트
  - 백테스트 엑셀: metrics 시트 (모델별 MAPE 등)
출력:
  - web/data.js  (window.SMP_DATA = {...})  ← index.html 이 <script src>로 로드

사용:
  python build_dashboard_data.py \
    --forecast forecasts/SMP_forecast_2026-06-12_issue0610.xlsx \
    --backtest SMP_2026_05_backtest.xlsx \
    --out web/data.js
"""
from __future__ import annotations
import argparse, json, datetime as dt
from pathlib import Path
import openpyxl


def _rows(ws):
    it = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(it)]
    for r in it:
        if all(c is None for c in r):
            continue
        yield dict(zip(header, r))


def _num(v, nd=2):
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def load_forecast(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    meta = {}
    if "meta" in wb.sheetnames:
        for row in _rows(wb["meta"]):
            meta[str(row.get("key"))] = row.get("value")

    ko = {}  # (region, model_id) -> {ko, name}
    for row in _rows(wb["summary"]):
        ko[(row["region"], row["model_id"])] = {
            "name_ko": row.get("model_ko") or row.get("model_name"),
            "name": row.get("model_name"),
        }

    # region -> model_id -> {hour_end: {p10,p50,p90}}
    fc: dict = {}
    order: dict = {}
    for row in _rows(wb["all_model_forecasts"]):
        region, mid = row["region"], row["model_id"]
        h = int(row["hour_end"])
        fc.setdefault(region, {}).setdefault(mid, {})[h] = {
            "p10": _num(row.get("p10")),
            "p50": _num(row.get("p50")),
            "p90": _num(row.get("p90")),
        }
        order.setdefault(region, [])
        if mid not in order[region]:
            order[region].append(mid)
    wb.close()
    return {"meta": meta, "ko": ko, "fc": fc, "order": order}


def load_backtest(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    metrics: dict = {}
    for row in _rows(wb["metrics"]):
        mape_key = next((k for k in row if k and k.startswith("mape")), None)
        metrics[(row["region"], row["model_id"])] = {
            "mape": _num(row.get(mape_key)) if mape_key else None,
            "mae": _num(row.get("mae")),
            "rmse": _num(row.get("rmse")),
            "smape": _num(row.get("smape_pct")),
        }
    wb.close()
    return metrics


def build(forecast: Path, backtest: Path | None, champion: str, risk: str) -> dict:
    f = load_forecast(forecast)
    bt = load_backtest(backtest) if backtest and backtest.exists() else {}

    regions = {}
    for region, mids in f["order"].items():
        hours = sorted({h for mid in mids for h in f["fc"][region][mid]})
        models = []
        for mid in mids:
            series = f["fc"][region][mid]
            p50 = [series.get(h, {}).get("p50") for h in hours]
            p10 = [series.get(h, {}).get("p10") for h in hours]
            p90 = [series.get(h, {}).get("p90") for h in hours]
            valid = [v for v in p50 if v is not None]
            m = bt.get((region, mid), {})
            role = ("champion" if mid == champion else
                    "ensemble" if mid == risk else "model")
            models.append({
                "id": mid,
                "name_ko": f["ko"].get((region, mid), {}).get("name_ko", mid),
                "name": f["ko"].get((region, mid), {}).get("name", mid),
                "role": role,
                "p50": p50, "p10": p10, "p90": p90,
                "avg": round(sum(valid) / len(valid), 1) if valid else None,
                "mape": m.get("mape"), "mae": m.get("mae"),
                "rmse": m.get("rmse"), "smape": m.get("smape"),
            })
        regions[region] = {"hours": hours, "models": models}

    meta = f["meta"]
    return {
        "meta": {
            "target_date": str(meta.get("target_date", "")),
            "issue_ts": str(meta.get("issue_ts_kst", "")),
            "champion_id": champion,
            "ensemble_id": risk,
            "unit": "KRW/kWh",
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "backtest_period": backtest.stem if backtest else "",
        },
        "regions": regions,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forecast", required=True, type=Path)
    ap.add_argument("--backtest", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("web/data.js"))
    ap.add_argument("--champion", default="MDL-04")
    ap.add_argument("--ensemble", default="MDL-07")
    args = ap.parse_args()

    data = build(args.forecast, args.backtest, args.champion, args.ensemble)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = "window.SMP_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n"
    args.out.write_text(payload, encoding="utf-8")
    n = sum(len(r["models"]) for r in data["regions"].values())
    print(f"wrote {args.out}  | regions={list(data['regions'])}  models={n}  "
          f"target={data['meta']['target_date']}")


if __name__ == "__main__":
    main()
