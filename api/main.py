from __future__ import annotations

import os
import json
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db
from .revenue_engine import EFFECTIVE_CAPACITY_RATIO, RevenueInputs, estimate_revenue


app = FastAPI(title="SMP VPP Phase 2 API", version="0.1.0")

raw_origins = [
    item.strip()
    for item in os.getenv("VPP_ALLOWED_ORIGINS", "http://localhost:8080").split(",")
    if item.strip()
]
origins = [item for item in raw_origins if "*" not in item]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://.*\.github\.io" if any("*" in o for o in raw_origins) else None,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class RevenueRequest(BaseModel):
    region: Literal["LAND", "JEJU"] = "JEJU"
    target_date: date | None = None
    scenario: Literal["conservative", "base", "optimistic"] = "base"
    view_mode: Literal["day", "month"] = "day"
    smp: list[float] | None = Field(default=None, description="24 hourly SMP values in KRW/kWh")
    model_id: str = "MDL-07"
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
    persist: bool = False


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metadata/effective-capacity")
def effective_capacity_table() -> dict:
    return {
        "source": "research_ESS.md",
        "rcp_jeju_krw_per_kw_h": 22.05,
        "ratios_pct": EFFECTIVE_CAPACITY_RATIO,
    }


@app.get("/forecasts")
def forecasts(
    region: Literal["LAND", "JEJU"] = Query(...),
    target_date: date = Query(...),
    model_id: str | None = None,
) -> dict:
    params: dict = {"region": region, "target_date": target_date}
    model_sql = ""
    if model_id:
        model_sql = "AND model_id = %(model_id)s"
        params["model_id"] = model_id
    result = db.rows(
        f"""
        SELECT region, target_date, hour_end, model_id, model_name,
               p10, p25, p50, p75, p90, unit, issue_ts_kst
        FROM vpp.smp_forecast
        WHERE region = %(region)s AND target_date = %(target_date)s
        {model_sql}
        ORDER BY model_id, hour_end
        """,
        params,
    )
    return {"rows": result}


@app.get("/actuals")
def actuals(
    region: Literal["LAND", "JEJU"] = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
) -> dict:
    result = db.rows(
        """
        SELECT region, target_date, hour_end, ts_end, smp
        FROM vpp.smp_actual
        WHERE region = %(region)s
          AND target_date BETWEEN %(start_date)s AND %(end_date)s
        ORDER BY target_date, hour_end
        """,
        {"region": region, "start_date": start_date, "end_date": end_date},
    )
    return {"rows": result}


@app.get("/scores")
def scores(
    region: Literal["LAND", "JEJU"] = Query(...),
    target_date: date | None = None,
) -> dict:
    params: dict = {"region": region}
    date_sql = ""
    if target_date:
        date_sql = "AND target_date = %(target_date)s"
        params["target_date"] = target_date
    result = db.rows(
        f"""
        SELECT target_date, region, model_id, model_name, model_ko,
               n_hours, actual_avg, forecast_avg, bias, mae, rmse, mape, smape, score
        FROM vpp.forecast_score
        WHERE region = %(region)s {date_sql}
        ORDER BY target_date DESC, score DESC NULLS LAST
        """,
        params,
    )
    return {"rows": result}


def _load_smp_from_db(region: str, target_date: date, model_id: str) -> list[float]:
    result = db.rows(
        """
        SELECT p50
        FROM vpp.smp_forecast
        WHERE region = %(region)s
          AND target_date = %(target_date)s
          AND model_id = %(model_id)s
        ORDER BY hour_end
        """,
        {"region": region, "target_date": target_date, "model_id": model_id},
    )
    return [float(row["p50"]) for row in result if row["p50"] is not None]


@app.post("/revenue/estimate")
def revenue_estimate(req: RevenueRequest) -> dict:
    smp = req.smp
    if smp is None:
        if not req.target_date:
            raise HTTPException(status_code=400, detail="target_date is required when smp is omitted")
        smp = _load_smp_from_db(req.region, req.target_date, req.model_id)
    if not smp:
        raise HTTPException(status_code=404, detail="No SMP series available")

    inputs = RevenueInputs(
        region=req.region,
        target_date=req.target_date,
        scenario=req.scenario,
        view_mode=req.view_mode,
        pv_capacity_mw=req.pv_capacity_mw,
        wind_capacity_mw=req.wind_capacity_mw,
        generation_capacity_factor=req.generation_capacity_factor,
        ess_energy_mwh=req.ess_energy_mwh,
        ess_power_mw=req.ess_power_mw,
        ess_efficiency=req.ess_efficiency,
        subsidy_krw_per_kwh=req.subsidy_krw_per_kwh,
        rcp_krw_per_kw_h=req.rcp_krw_per_kw_h,
        rpcf=req.rpcf,
        mape_pct=req.mape_pct,
        dispatch_instruction=req.dispatch_instruction,
        bid_floor_krw_per_kwh=req.bid_floor_krw_per_kwh,
    )
    try:
        payload = estimate_revenue(smp, inputs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if req.persist and req.target_date:
        out = payload["outputs"]
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vpp.revenue_run (
                        region, target_date, scenario, view_mode, rcp_krw_per_kw_h, rpcf,
                        market_revenue_krw, ess_revenue_krw, capacity_revenue_krw,
                        subsidy_revenue_krw, imbalance_penalty_krw, total_revenue_krw,
                        inputs, outputs
                    )
                    VALUES (
                        %(region)s, %(target_date)s, %(scenario)s, %(view_mode)s,
                        %(rcp)s, %(rpcf)s, %(market)s, %(ess)s, %(capacity)s,
                        %(subsidy)s, %(penalty)s, %(total)s,
                        %(inputs)s::jsonb, %(outputs)s::jsonb
                    )
                    """,
                    {
                        "region": req.region,
                        "target_date": req.target_date,
                        "scenario": req.scenario,
                        "view_mode": req.view_mode,
                        "rcp": req.rcp_krw_per_kw_h,
                        "rpcf": req.rpcf,
                        "market": out["market_revenue_krw"],
                        "ess": out["ess_revenue_krw"],
                        "capacity": out["capacity_revenue_krw"],
                        "subsidy": out["subsidy_revenue_krw"],
                        "penalty": out["imbalance_penalty_krw"],
                        "total": out["total_revenue_krw"],
                        "inputs": json.dumps(payload["inputs"], ensure_ascii=False),
                        "outputs": json.dumps(out, ensure_ascii=False),
                    },
                )
    return payload
