CREATE TABLE IF NOT EXISTS vpp.revenue_history (
    target_date DATE NOT NULL,
    region TEXT NOT NULL CHECK (region IN ('LAND', 'JEJU')),
    source TEXT NOT NULL CHECK (source IN ('actual', 'forecast')),
    issue_hour TEXT,
    model_id TEXT NOT NULL,
    model_name TEXT,
    n_hours SMALLINT,
    avg_smp NUMERIC(12, 4),
    low_smp NUMERIC(12, 4),
    high_smp NUMERIC(12, 4),
    spread_smp NUMERIC(12, 4),
    mape_pct NUMERIC(12, 4),
    pv_effective_mw NUMERIC(12, 4),
    ess_effective_mw NUMERIC(12, 4),
    market_revenue_krw NUMERIC(18, 4),
    ess_revenue_krw NUMERIC(18, 4),
    capacity_revenue_krw NUMERIC(18, 4),
    subsidy_revenue_krw NUMERIC(18, 4),
    imbalance_penalty_krw NUMERIC(18, 4),
    total_revenue_krw NUMERIC(18, 4),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (target_date, region, source, model_id)
);

CREATE INDEX IF NOT EXISTS idx_revenue_history_date_region
    ON vpp.revenue_history (target_date DESC, region, source);
