-- Optional TimescaleDB setup. Run this only on a PostgreSQL instance where the
-- TimescaleDB extension is installed.

CREATE EXTENSION IF NOT EXISTS timescaledb;

SELECT create_hypertable(
    'vpp.smp_actual',
    'target_date',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

SELECT create_hypertable(
    'vpp.smp_forecast',
    'target_date',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

SELECT create_hypertable(
    'vpp.forecast_score',
    'target_date',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

