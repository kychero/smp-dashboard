# Phase 2 DB/FastAPI Setup

Last updated: 2026-06-15

## 1. Scope

Phase 2 adds a backend beside the existing file-based daily SMP pipeline.

- Keep `run_daily.sh`, CSV/Excel outputs, and GitHub Pages intact.
- Add PostgreSQL tables for SMP actuals, forecasts, forecast scores, resources, revenue runs, and future settlements.
- Add FastAPI endpoints for forecasts, actuals, scores, ESS/VPP revenue estimation, and effective-capacity reference data.

## 2. Install API Dependencies

```bash
cd /home/opc/smp/repo
/home/opc/smp/venv/bin/pip install -r requirements-api.txt
```

## 3. Create Database Schema

With Docker:

```bash
cd /home/opc/smp/repo
docker compose -f docker-compose.phase2.yml up -d
export DATABASE_URL=postgresql://smp:smp@127.0.0.1:5432/smp
```

Manual PostgreSQL setup:

Run the standard PostgreSQL schema first:

```bash
psql "$DATABASE_URL" -f db/001_init.sql
```

If the database has TimescaleDB installed, optionally run:

```bash
psql "$DATABASE_URL" -f db/002_timescaledb_optional.sql
```

## 4. Configure API

```bash
cp api/.env.example api/.env
```

Then set:

```text
DATABASE_URL=postgresql://smp:smp@127.0.0.1:5432/smp
VPP_ALLOWED_ORIGINS=http://localhost:8080,https://*.github.io
```

## 5. Ingest Current File Artifacts

```bash
cd /home/opc/smp/repo
/home/opc/smp/venv/bin/python -m api.ingest_files
```

This loads:

- `processed/smp_actuals_cache.csv` -> `vpp.smp_actual`
- `processed/latest_next_day_forecast.csv` -> `vpp.smp_forecast`
- `/home/opc/smp/data/forecast_score_history.csv` -> `vpp.forecast_score`

## 6. Run FastAPI

```bash
cd /home/opc/smp/repo
/home/opc/smp/venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Useful checks:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/metadata/effective-capacity
```

Revenue estimate with direct SMP input:

```bash
curl -X POST http://127.0.0.1:8000/revenue/estimate \
  -H 'Content-Type: application/json' \
  -d '{
    "region":"JEJU",
    "target_date":"2026-06-15",
    "scenario":"base",
    "view_mode":"day",
    "smp":[90,88,85,84,86,92,105,115,120,118,110,95,80,70,65,72,90,120,140,150,145,130,115,100],
    "pv_capacity_mw":5,
    "ess_energy_mwh":10,
    "ess_power_mw":5,
    "dispatch_instruction":false
  }'
```

## 7. ESS/CP Assumptions

The revenue engine uses `/home/opc/smp/docs/research_ESS.md`:

- Jeju RCP: `22.05 KRW/kW-h`
- RPCF default: `1.0`
- Imbalance tolerance: `1.5%` when dispatch instruction is ON, otherwise `6%`
- Effective capacity: monthly '25/'26 Jeju PV/WIND/ESS table
- ESS duration bucket: 2h, 4h, 6h, 8h selected from `ess_energy_mwh / ess_power_mw`

Current output is an estimate, not settlement-grade accounting. Uplift and final market settlement rules still require source-system integration.
