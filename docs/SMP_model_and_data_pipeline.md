# SMP Forecast Model and Data Pipeline

Last updated: 2026-06-14

## 1. Purpose

This document describes the SMP day-ahead forecasting implementation currently running on the server:

- forecast target: D+1 hourly SMP, 24 hour-ending rows per region
- regions: `LAND`, `JEJU`
- output: model-level P10/P25/P50/P75/P90 and final ensemble
- dashboard data: `repo/web/data.js`

The implementation is aligned with `SMP_detail_design.docx` where possible. Some design inputs require external API keys or upstream systems and are currently implemented as optional feature columns.

## 2. Main Files

| Path | Role |
| --- | --- |
| `/home/opc/smp/run_daily.sh` | Daily orchestration: pull repo, collect external features, run forecast, build dashboard data, push `web/data.js` |
| `/home/opc/smp/repo/daily_smp_agent.py` | Daily SMP data loading, feature assembly, model execution, forecast Excel/CSV creation |
| `/home/opc/smp/repo/run_smp_backtest.py` | Feature engineering, model definitions, backtest utilities |
| `/home/opc/smp/repo/collect_external_features.py` | Weather and PV proxy collector for `external_features.csv` |
| `/home/opc/smp/repo/build_dashboard_data.py` | Forecast/backtest Excel to `web/data.js` |
| `/home/opc/smp/agent_config.json` | Local runtime config |
| `/home/opc/smp/data/external_features.csv` | Optional exogenous input feature file |

## 3. Current Data Sources

### 3.1 SMP Actuals

Source:

- EPSIS hourly SMP AJAX endpoint
- URL used by code: `https://epsis.kpx.or.kr/epsisnew/selectEkmaSmpShd.ajax`
- Referer: `https://epsis.kpx.or.kr/epsisnew/selectEkmaSmpShdChart.do`

Collector:

- `daily_smp_agent.py`
- function: `_fetch_epsis_chunk()`

Output cache:

- `/home/opc/smp/repo/processed/smp_actuals_cache.csv`

Columns:

| Column | Meaning |
| --- | --- |
| `region` | `LAND` or `JEJU` |
| `target_date` | SMP date |
| `hour_end` | hour-ending, 1 to 24 |
| `hour_index_0_23` | helper hour index |
| `target_ts_end` | timestamp at hour end |
| `smp` | actual SMP, KRW/kWh |

Current cache status as of this document:

- period: `2025-09-01` to `2026-06-13`
- rows: 13,728
- regions: `LAND`, `JEJU`

The runtime setting is `train_years: 3`, and `daily_smp_agent.py` now backfills missing historical cache ranges when `history_start` is earlier than the cached first date. This is needed for the design target of 3-year training.

### 3.2 Weather Features

Source:

- Open-Meteo Forecast API: `https://api.open-meteo.com/v1/forecast`
- Open-Meteo Historical Archive API: `https://archive-api.open-meteo.com/v1/archive`

Collector:

- `/home/opc/smp/repo/collect_external_features.py`

Variables collected:

| Output Column | Open-Meteo Variable | Meaning |
| --- | --- | --- |
| `temp_pop_weighted` | `temperature_2m` | region-weighted temperature |
| `irradiance_avg` | `shortwave_radiation` | region-weighted shortwave radiation |
| `wind_speed_avg` | `wind_speed_10m` | region-weighted 10m wind speed |

Region weighting points:

| Region | Points |
| --- | --- |
| `LAND` | Seoul, Daejeon, Daegu, Busan, Gwangju |
| `JEJU` | Jeju, Seogwipo |

The script uses weighted averages because the model operates at `LAND` and `JEJU` region granularity, not station granularity.

### 3.3 Solar Generation Feature

Current source:

- proxy generated from `irradiance_avg`

Collector:

- `collect_external_features.py`
- option: `--pv-proxy`

Output column:

- `pv_forecast_total`

Formula:

```text
capacity_factor = clip(irradiance_avg / 1000 * 0.82, upper=0.90)
pv_forecast_total = capacity_factor * regional_capacity_mw
```

Default proxy capacities:

| Region | Capacity MW |
| --- | ---: |
| `LAND` | 25,000 |
| `JEJU` | 1,100 |

Important limitation:

- This is not measured KPX solar generation.
- It is a weather-based proxy used so the model can consume the design feature shape before the real DS-06/M2 solar forecast is available.
- If a real PV CSV is supplied, it can override the proxy via `--pv-csv`.

### 3.4 Demand Forecast

Design source:

- KPX/public-data day-ahead demand forecast, referred to as DS-01 in the design document.

Current implementation status:

- Automatically collected from the public EPSIS real-time supply/demand page when `--epsis-demand` is used.
- Source endpoint: `https://epsis.kpx.or.kr/epsisnew/selectEkgeEpsMepRealGridAjax.ajax`
- Source page: `https://epsis.kpx.or.kr/epsisnew/selectEkgeEpsMepRealChart.do?menuId=030300`
- Source field: `현재부하(MW)`, delivered at 5-minute resolution.
- Collector aggregation: 5-minute values are averaged into `hour_end` 1 to 24.
- EPSIS exposes actual/near-real-time system load, not a true D+1 demand forecast.
- For future dates such as tomorrow, the collector fills `demand_forecast_d1` from existing demand history using same-hour lag-7 first, then same weekday/hour average, then same-hour average.
- The public EPSIS response does not expose separate LAND/JEJU demand, so the same system load input is duplicated for both model regions.
- A real day-ahead demand CSV can still override or supplement this field when available.

Expected demand CSV schema:

```csv
region,target_date,hour_end,demand_forecast_d1
LAND,2026-06-15,1,....
JEJU,2026-06-15,1,....
```

Merge command:

```bash
/home/opc/smp/repo/collect_external_features.py \
  --start-date 2025-09-01 \
  --end-date 2026-06-15 \
  --pv-proxy \
  --epsis-demand \
  --out /home/opc/smp/data/external_features.csv
```

### 3.5 External Feature File

Path:

```text
/home/opc/smp/data/external_features.csv
```

Required keys:

```text
region,target_date,hour_end
```

Supported optional columns:

```text
demand_forecast_d1
pv_forecast_total
wind_forecast_total
lng_heat_price
brent_lag_10d
usdkrw_lag_1d
coal_index_lag
temp_pop_weighted
irradiance_avg
wind_speed_avg
maintenance_capacity
interconnector_flag
```

Current generated status:

- period: `2025-09-01` to `2026-06-15`
- rows: 13,824
- populated: `demand_forecast_d1`, `pv_forecast_total`, `temp_pop_weighted`, `irradiance_avg`, `wind_speed_avg`
- empty: `wind_forecast_total`

## 4. Daily Pipeline

Daily script:

```bash
/home/opc/smp/run_daily.sh
```

Current flow:

1. Create log/output directories.
2. Activate `/home/opc/smp/venv`.
3. Pull latest GitHub repo.
4. Generate or merge external features for D+1:

```bash
collect_external_features.py \
  --start-date "$FEATURE_START_DATE" \
  --end-date "$TARGET_DATE" \
  --pv-proxy \
  --epsis-demand \
  --merge-existing \
  --out /home/opc/smp/data/external_features.csv
```

5. Run `daily_smp_agent.py`.
6. Find latest forecast Excel.
7. Find existing `SMP_*_backtest.xlsx` if available.
8. Run `build_dashboard_data.py`.
9. Commit and push `web/data.js`.

## 5. Feature Engineering

Feature builder:

- `run_smp_backtest.py`
- function: `build_features()`

### 5.1 Base SMP Features

| Feature | Description |
| --- | --- |
| `smp_lag_48h` to `smp_lag_672h` | past SMP lag features |
| `same_hour_ma_7d_safe` | same hour moving average over 7 prior observations |
| `same_hour_ma_30d_safe` | same hour moving average over 30 prior observations |
| `same_hour_std_30d_safe` | same hour volatility |
| `same_hour_q10_30d_safe` | same hour lower historical quantile |
| `same_hour_q90_30d_safe` | same hour upper historical quantile |
| `smp_roll_mean_168h_safe` | rolling 168-hour mean, shifted to avoid leakage |
| `smp_roll_std_168h_safe` | rolling 168-hour standard deviation |
| `smp_roll_mean_720h_safe` | rolling 720-hour mean |
| `scarcity_proxy` | SMP-only proxy for tight supply conditions |

`scarcity_proxy`:

```text
0.50 * smp_lag_168h
+ 0.25 * same_hour_ma_7d_safe
+ 0.25 * same_hour_ma_30d_safe
+ 2.0 * is_peak_hour
```

### 5.2 Calendar Features

| Feature | Description |
| --- | --- |
| `hour_sin`, `hour_cos` | cyclic hour |
| `dow_sin`, `dow_cos` | cyclic day of week |
| `month_sin`, `month_cos` | cyclic month |
| `doy_sin`, `doy_cos` | cyclic day of year |
| `is_weekend` | weekend flag |
| `is_holiday` | Korean holiday flag |
| `is_peak_hour` | 18~22 hour-ending |
| `is_solar_hour` | 10~16 hour-ending |
| `holiday_bridge_flag` | adjacent to holiday |
| `seollal_chuseok_window` | major holiday window flag |

### 5.3 Design Optional Features

The model declares F2~F7 design features but only uses optional columns that contain at least one non-null value.

Function:

```python
active_features(df)
```

This avoids breaking the pipeline when demand, fuel, or grid-event feeds are not yet available.

Currently active optional features when `external_features.csv` is present:

```text
pv_forecast_total
temp_pop_weighted
hdd
cdd
irradiance_avg
wind_speed_avg
peak_flag
holiday_bridge_flag
seollal_chuseok_window
```

## 6. Model Definitions

Model code:

- `run_smp_backtest.py`

### MDL-01 Seasonal Naive

Input:

```text
smp_lag_168h
same_hour_ma_7d_safe
```

Formula:

```text
P50 = 0.5 * smp_lag_168h + 0.5 * same_hour_ma_7d_safe
```

Quantiles:

- generated from training residual empirical quantiles

Role:

- benchmark baseline

### MDL-02 SARIMAX Proxy: Ridge ARX

Current implementation:

- Ridge regression with imputation and scaling
- Named proxy because `statsmodels` is not installed in the current venv.

Input:

- `active_features(train)`
- includes SMP lag/calendar features and any available optional design features

Role:

- stable linear autoregressive/exogenous baseline

### MDL-03 Fundamental / Netload Isotonic Curve

Current behavior:

- if `netload` has enough non-null values: use `netload`
- otherwise: fall back to `scarcity_proxy`

Model:

- `IsotonicRegression(out_of_bounds="clip")`

Input:

```text
netload
```

or fallback:

```text
scarcity_proxy
```

Role:

- interpretable supply-curve style model

### MDL-04 LightGBM Point

Input:

- `active_features(train)`

Model:

- LightGBM `LGBMRegressor(objective="mae")`
- fallback to `HistGradientBoostingRegressor` if LightGBM is unavailable

Output:

- point forecast used as P50
- residual quantiles used for P10/P25/P75/P90

Role:

- champion candidate / current dashboard champion

### MDL-05 LightGBM Quantile

Input:

- `active_features(train)`

Model:

- five LightGBM quantile models

Quantiles:

```text
P10, P25, P50, P75, P90
```

Post-processing:

- quantile crossing is corrected by sorting quantile columns row-wise

Role:

- native uncertainty model

### MDL-06 MLP Proxy for TFT

Current implementation:

- `MLPRegressor(hidden_layer_sizes=(64, 32))`
- proxy for the TFT model in the design document

Input:

- `active_features(train)`

Role:

- nonlinear neural challenger

Known warning:

- The MLP can emit a convergence warning at `max_iter=260`.
- The forecast still completes.

### MDL-07 Validation-Weighted Ensemble

Input:

- MDL-01 through MDL-06 outputs
- MDL-08 also joins when its profile file is available

Weighting:

```text
weight = inverse validation MAE, normalized by region
```

Output:

- weighted P10/P25/P50/P75/P90

Role:

- operational ensemble and dashboard risk model

### MDL-08 Research Workbook Profile

Required file:

```text
/home/opc/smp/repo/source_docs/mdl08_research_smp_profile.csv
```

Current status:

- not active because the file is not present
- `daily_smp_agent.py` skips MDL-08 when the profile is missing

Expected columns:

```text
month,hour_end,smp_profile
```

## 7. Backtest and Dashboard Data

Backtest Excel:

```text
/home/opc/smp/data/SMP_2026_05_backtest.xlsx
```

Expected sheets:

| Sheet | Used For |
| --- | --- |
| `metrics` | model MAPE/MAE/RMSE/SMAPE |
| `predictions` | actual vs model P50 time series |
| `hourly_metrics` | optional hourly accuracy |
| `ensemble_weights` | optional ensemble diagnostics |

Dashboard data builder:

```bash
/home/opc/smp/venv/bin/python /home/opc/smp/repo/build_dashboard_data.py \
  --forecast /home/opc/smp/data/daily_outputs/SMP_forecast_YYYY-MM-DD_issue0600.xlsx \
  --backtest /home/opc/smp/data/SMP_2026_05_backtest.xlsx \
  --out /home/opc/smp/repo/web/data.js
```

## 8. Current Limitations

1. Demand forecast is not automatically collected yet.
   - `demand_forecast_d1` is supported but empty until a KPX/API export is supplied.

2. Solar generation is currently a proxy.
   - `pv_forecast_total` is derived from irradiance, not actual measured/forecast PV.

3. True SARIMAX is not implemented.
   - `statsmodels` is absent from the venv.
   - MDL-02 is a Ridge ARX proxy.

4. TFT is not implemented.
   - MDL-06 is an MLP proxy.

5. Fuel and grid-event features are declared but not populated.
   - LNG, Brent, FX, maintenance, and interconnector feeds need source files or APIs.

## 9. How to Refresh External Features

For the current available training period:

```bash
/home/opc/smp/repo/collect_external_features.py \
  --start-date 2025-09-01 \
  --end-date 2026-06-15 \
  --pv-proxy \
  --out /home/opc/smp/data/external_features.csv
```

For daily update only:

```bash
/home/opc/smp/repo/collect_external_features.py \
  --start-date 2026-06-15 \
  --end-date 2026-06-15 \
  --pv-proxy \
  --merge-existing \
  --out /home/opc/smp/data/external_features.csv
```

## 10. How to Add Real Demand and Solar

Demand CSV:

```csv
region,target_date,hour_end,demand_forecast_d1
LAND,2026-06-15,1,65000
JEJU,2026-06-15,1,900
```

Solar/wind CSV:

```csv
region,target_date,hour_end,pv_forecast_total,wind_forecast_total
LAND,2026-06-15,12,15000,1200
JEJU,2026-06-15,12,650,180
```

Merge command:

```bash
/home/opc/smp/repo/collect_external_features.py \
  --start-date 2025-09-01 \
  --end-date 2026-06-15 \
  --pv-proxy \
  --demand-csv /path/to/demand.csv \
  --pv-csv /path/to/pv_wind.csv \
  --out /home/opc/smp/data/external_features.csv
```

After merging real data, rerun:

```bash
/home/opc/smp/repo/daily_smp_agent.py --config /home/opc/smp/agent_config.json
```

Then rebuild dashboard data:

```bash
/home/opc/smp/venv/bin/python /home/opc/smp/repo/build_dashboard_data.py \
  --forecast /home/opc/smp/data/daily_outputs/SMP_forecast_2026-06-15_issue0600.xlsx \
  --backtest /home/opc/smp/data/SMP_2026_05_backtest.xlsx \
  --out /home/opc/smp/repo/web/data.js
```
