-- Phase 2 VPP backend schema.
-- Standard PostgreSQL first; TimescaleDB hypertables are optional and live in
-- 002_timescaledb_optional.sql so local Postgres can run this file unchanged.

CREATE SCHEMA IF NOT EXISTS vpp;

CREATE TABLE IF NOT EXISTS vpp.tenant (
    tenant_id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vpp.app_user (
    user_id BIGSERIAL PRIMARY KEY,
    tenant_id BIGINT NOT NULL REFERENCES vpp.tenant(tenant_id) ON DELETE CASCADE,
    email TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('operator', 'owner', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vpp.resource (
    resource_id BIGSERIAL PRIMARY KEY,
    tenant_id BIGINT REFERENCES vpp.tenant(tenant_id) ON DELETE SET NULL,
    owner_id BIGINT REFERENCES vpp.app_user(user_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('PV', 'WIND', 'ESS', 'HYBRID')),
    region TEXT NOT NULL CHECK (region IN ('LAND', 'JEJU')),
    capacity_mw NUMERIC(12, 4) NOT NULL DEFAULT 0,
    ess_power_mw NUMERIC(12, 4),
    ess_energy_mwh NUMERIC(12, 4),
    location TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vpp.smp_actual (
    region TEXT NOT NULL CHECK (region IN ('LAND', 'JEJU')),
    target_date DATE NOT NULL,
    hour_end SMALLINT NOT NULL CHECK (hour_end BETWEEN 1 AND 24),
    ts_end TIMESTAMPTZ,
    smp NUMERIC(12, 4) NOT NULL,
    source TEXT NOT NULL DEFAULT 'EPSIS',
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (region, target_date, hour_end)
);

CREATE TABLE IF NOT EXISTS vpp.smp_forecast (
    region TEXT NOT NULL CHECK (region IN ('LAND', 'JEJU')),
    target_date DATE NOT NULL,
    hour_end SMALLINT NOT NULL CHECK (hour_end BETWEEN 1 AND 24),
    model_id TEXT NOT NULL,
    model_name TEXT,
    p10 NUMERIC(12, 4),
    p25 NUMERIC(12, 4),
    p50 NUMERIC(12, 4),
    p75 NUMERIC(12, 4),
    p90 NUMERIC(12, 4),
    unit TEXT NOT NULL DEFAULT 'KRW/kWh',
    issue_ts_kst TIMESTAMPTZ,
    forecast_file TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (region, target_date, hour_end, model_id)
);

CREATE TABLE IF NOT EXISTS vpp.forecast_score (
    target_date DATE NOT NULL,
    region TEXT NOT NULL CHECK (region IN ('LAND', 'JEJU')),
    issue_hour TEXT,
    model_id TEXT NOT NULL,
    model_name TEXT,
    model_ko TEXT,
    n_hours SMALLINT,
    actual_avg NUMERIC(12, 4),
    forecast_avg NUMERIC(12, 4),
    bias NUMERIC(12, 4),
    mae NUMERIC(12, 4),
    rmse NUMERIC(12, 4),
    mape NUMERIC(12, 4),
    smape NUMERIC(12, 4),
    score NUMERIC(12, 4),
    forecast_file TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (target_date, region, model_id)
);

CREATE TABLE IF NOT EXISTS vpp.revenue_run (
    run_id BIGSERIAL PRIMARY KEY,
    tenant_id BIGINT REFERENCES vpp.tenant(tenant_id) ON DELETE SET NULL,
    resource_id BIGINT REFERENCES vpp.resource(resource_id) ON DELETE SET NULL,
    region TEXT NOT NULL CHECK (region IN ('LAND', 'JEJU')),
    target_date DATE NOT NULL,
    scenario TEXT NOT NULL CHECK (scenario IN ('conservative', 'base', 'optimistic')),
    view_mode TEXT NOT NULL CHECK (view_mode IN ('day', 'month')),
    rcp_krw_per_kw_h NUMERIC(12, 4) NOT NULL,
    rpcf NUMERIC(8, 4) NOT NULL,
    market_revenue_krw NUMERIC(18, 4) NOT NULL,
    ess_revenue_krw NUMERIC(18, 4) NOT NULL,
    capacity_revenue_krw NUMERIC(18, 4) NOT NULL,
    subsidy_revenue_krw NUMERIC(18, 4) NOT NULL,
    imbalance_penalty_krw NUMERIC(18, 4) NOT NULL,
    total_revenue_krw NUMERIC(18, 4) NOT NULL,
    inputs JSONB NOT NULL,
    outputs JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vpp.settlement (
    settlement_id BIGSERIAL PRIMARY KEY,
    resource_id BIGINT REFERENCES vpp.resource(resource_id) ON DELETE SET NULL,
    region TEXT NOT NULL CHECK (region IN ('LAND', 'JEJU')),
    settlement_date DATE NOT NULL,
    energy_revenue_krw NUMERIC(18, 4) NOT NULL DEFAULT 0,
    uplift_revenue_krw NUMERIC(18, 4) NOT NULL DEFAULT 0,
    capacity_revenue_krw NUMERIC(18, 4) NOT NULL DEFAULT 0,
    imbalance_penalty_krw NUMERIC(18, 4) NOT NULL DEFAULT 0,
    total_revenue_krw NUMERIC(18, 4) NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_smp_actual_date_region
    ON vpp.smp_actual (target_date DESC, region);

CREATE INDEX IF NOT EXISTS idx_smp_forecast_date_region_model
    ON vpp.smp_forecast (target_date DESC, region, model_id);

CREATE INDEX IF NOT EXISTS idx_forecast_score_date_region
    ON vpp.forecast_score (target_date DESC, region);

CREATE INDEX IF NOT EXISTS idx_revenue_run_date_region
    ON vpp.revenue_run (target_date DESC, region, scenario);

