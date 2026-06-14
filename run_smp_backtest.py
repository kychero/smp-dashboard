from __future__ import annotations

import json
import math
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYDEPS = ROOT / ".pydeps"
if PYDEPS.exists():
    sys.path.insert(0, str(PYDEPS))

import numpy as np
import pandas as pd

from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from lightgbm import LGBMRegressor

    HAS_LIGHTGBM = True
except Exception:
    HAS_LIGHTGBM = False


RAW = ROOT / "raw"
OUT = ROOT / "processed"
OUT.mkdir(parents=True, exist_ok=True)
RESEARCH_PROFILE_PATH = ROOT / "source_docs" / "mdl08_research_smp_profile.csv"

TRAIN_START = pd.Timestamp("2023-05-01")
VALID_START = pd.Timestamp("2026-04-01")
TEST_START = pd.Timestamp("2026-05-01")
TEST_END_EXCL = pd.Timestamp("2026-06-01")
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]

OPTIONAL_INPUT_FEATURES = [
    # F2 demand
    "demand_forecast_d1",
    # F3 renewable / net load
    "pv_forecast_total",
    "wind_forecast_total",
    # F4 fuel / FX
    "lng_heat_price",
    "brent_lag_10d",
    "usdkrw_lag_1d",
    "coal_index_lag",
    # F5 weather
    "temp_pop_weighted",
    "irradiance_avg",
    "wind_speed_avg",
    # F7 grid events
    "maintenance_capacity",
    "interconnector_flag",
]

DESIGN_DERIVED_FEATURES = [
    "demand_lag_168h",
    "peak_flag",
    "netload",
    "netload_ramp",
    "hdd",
    "cdd",
    "holiday_bridge_flag",
    "seollal_chuseok_window",
]


@dataclass(frozen=True)
class ModelResult:
    model_id: str
    model_name: str
    val: pd.DataFrame
    test: pd.DataFrame


ASSIGN_RE = re.compile(r'c(\d+)\s*=\s*textFormmat\("([^"]*)",count\)')
DATE_RE = re.compile(r'gridData\.push\(\{"Date":"([^"]+)"')


def to_float(value: str) -> float:
    value = value.strip().replace(",", "")
    if not value or value in {"-", "null", "None"}:
        return float("nan")
    return float(value)


def parse_epsis_js(path: Path, region: str) -> pd.DataFrame:
    vals: dict[int, float] = {}
    rows: list[dict[str, object]] = []

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        assign = ASSIGN_RE.search(line)
        if assign:
            vals[int(assign.group(1))] = to_float(assign.group(2))
            continue

        date_match = DATE_RE.search(line)
        if date_match:
            date = pd.to_datetime(date_match.group(1), format="%Y/%m/%d")
            for hour in range(1, 25):
                rows.append(
                    {
                        "region": region,
                        "target_date": date.date().isoformat(),
                        "hour_end": hour,
                        "hour_index_0_23": hour - 1,
                        "smp": vals.get(hour, np.nan),
                    }
                )
            vals = {}

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"No EPSIS rows parsed from {path}")
    df["target_date"] = pd.to_datetime(df["target_date"])
    df["target_ts_end"] = df["target_date"] + pd.to_timedelta(df["hour_end"], unit="h")
    df["smp"] = pd.to_numeric(df["smp"], errors="coerce")
    return df.sort_values(["region", "target_date", "hour_end"]).reset_index(drop=True)


def korean_holiday_dates() -> set[pd.Timestamp]:
    # Compact static table for the backtest window. It is only used as a calendar
    # signal; the source-data and lag features carry most of the model signal.
    dates = [
        # 2023
        "2023-05-05",
        "2023-05-27",
        "2023-05-29",
        "2023-06-06",
        "2023-08-15",
        "2023-09-28",
        "2023-09-29",
        "2023-09-30",
        "2023-10-02",
        "2023-10-03",
        "2023-10-09",
        "2023-12-25",
        # 2024
        "2024-01-01",
        "2024-02-09",
        "2024-02-10",
        "2024-02-11",
        "2024-02-12",
        "2024-03-01",
        "2024-04-10",
        "2024-05-05",
        "2024-05-06",
        "2024-05-15",
        "2024-06-06",
        "2024-08-15",
        "2024-09-16",
        "2024-09-17",
        "2024-09-18",
        "2024-10-03",
        "2024-10-09",
        "2024-12-25",
        # 2025
        "2025-01-01",
        "2025-01-28",
        "2025-01-29",
        "2025-01-30",
        "2025-03-01",
        "2025-03-03",
        "2025-05-05",
        "2025-05-06",
        "2025-06-06",
        "2025-08-15",
        "2025-10-03",
        "2025-10-05",
        "2025-10-06",
        "2025-10-07",
        "2025-10-08",
        "2025-10-09",
        "2025-12-25",
        # 2026
        "2026-01-01",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-03-01",
        "2026-03-02",
        "2026-05-05",
        "2026-05-24",
        "2026-05-25",
        "2026-06-06",
        "2026-08-15",
        "2026-08-17",
        "2026-09-24",
        "2026-09-25",
        "2026-09-26",
        "2026-10-03",
        "2026-10-05",
        "2026-10-09",
        "2026-12-25",
    ]
    return {pd.Timestamp(d) for d in dates}


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    holidays = korean_holiday_dates()
    all_parts = []
    for region, part in df.groupby("region", sort=False):
        p = part.sort_values(["target_date", "hour_end"]).copy()
        for col in OPTIONAL_INPUT_FEATURES:
            if col not in p.columns:
                p[col] = np.nan
        p["dow"] = p["target_date"].dt.dayofweek
        p["month"] = p["target_date"].dt.month
        p["dayofyear"] = p["target_date"].dt.dayofyear
        p["is_weekend"] = (p["dow"] >= 5).astype(int)
        p["is_holiday"] = p["target_date"].isin(holidays).astype(int)
        p["is_peak_hour"] = p["hour_end"].between(18, 22).astype(int)
        p["is_solar_hour"] = p["hour_end"].between(10, 16).astype(int)
        p["peak_flag"] = p["is_peak_hour"]
        p["holiday_bridge_flag"] = (
            p["target_date"].isin({d - pd.Timedelta(days=1) for d in holidays} |
                                  {d + pd.Timedelta(days=1) for d in holidays})
        ).astype(int)
        p["seollal_chuseok_window"] = (
            p["target_date"].isin(
                {
                    pd.Timestamp("2026-02-16"), pd.Timestamp("2026-02-17"),
                    pd.Timestamp("2026-02-18"), pd.Timestamp("2026-09-24"),
                    pd.Timestamp("2026-09-25"), pd.Timestamp("2026-09-26"),
                }
            )
        ).astype(int)
        p["hdd"] = np.maximum(18.0 - pd.to_numeric(p["temp_pop_weighted"], errors="coerce"), 0)
        p["cdd"] = np.maximum(pd.to_numeric(p["temp_pop_weighted"], errors="coerce") - 24.0, 0)

        for col, period, denom in [
            ("hour", p["hour_index_0_23"], 24),
            ("dow", p["dow"], 7),
            ("month", p["month"] - 1, 12),
            ("doy", p["dayofyear"] - 1, 366),
        ]:
            p[f"{col}_sin"] = np.sin(2 * np.pi * period / denom)
            p[f"{col}_cos"] = np.cos(2 * np.pi * period / denom)

        y = p["smp"]
        demand = pd.to_numeric(p["demand_forecast_d1"], errors="coerce")
        pv = pd.to_numeric(p["pv_forecast_total"], errors="coerce").fillna(0)
        wind = pd.to_numeric(p["wind_forecast_total"], errors="coerce").fillna(0)
        p["demand_lag_168h"] = demand.shift(168)
        p["netload"] = demand - pv - wind
        p["netload_ramp"] = p["netload"].diff()
        for lag in [48, 72, 96, 120, 144, 168, 336, 672]:
            p[f"smp_lag_{lag}h"] = y.shift(lag)

        p["smp_roll_mean_168h_safe"] = y.shift(48).rolling(168, min_periods=48).mean()
        p["smp_roll_std_168h_safe"] = y.shift(48).rolling(168, min_periods=48).std()
        p["smp_roll_mean_720h_safe"] = y.shift(48).rolling(720, min_periods=168).mean()

        by_hour = p.groupby("hour_end", group_keys=False)["smp"]
        p["same_hour_ma_7d_safe"] = by_hour.transform(
            lambda s: s.shift(2).rolling(7, min_periods=3).mean()
        )
        p["same_hour_ma_30d_safe"] = by_hour.transform(
            lambda s: s.shift(2).rolling(30, min_periods=10).mean()
        )
        p["same_hour_std_30d_safe"] = by_hour.transform(
            lambda s: s.shift(2).rolling(30, min_periods=10).std()
        )
        p["same_hour_q10_30d_safe"] = by_hour.transform(
            lambda s: s.shift(2).rolling(30, min_periods=10).quantile(0.10)
        )
        p["same_hour_q90_30d_safe"] = by_hour.transform(
            lambda s: s.shift(2).rolling(30, min_periods=10).quantile(0.90)
        )
        p["scarcity_proxy"] = (
            0.50 * p["smp_lag_168h"]
            + 0.25 * p["same_hour_ma_7d_safe"]
            + 0.25 * p["same_hour_ma_30d_safe"]
            + 2.0 * p["is_peak_hour"]
        )
        p["region"] = region
        all_parts.append(p)

    return pd.concat(all_parts, ignore_index=True)


BASE_FEATURES = [
    "smp_lag_48h",
    "smp_lag_72h",
    "smp_lag_96h",
    "smp_lag_120h",
    "smp_lag_144h",
    "smp_lag_168h",
    "smp_lag_336h",
    "smp_lag_672h",
    "same_hour_ma_7d_safe",
    "same_hour_ma_30d_safe",
    "same_hour_std_30d_safe",
    "same_hour_q10_30d_safe",
    "same_hour_q90_30d_safe",
    "smp_roll_mean_168h_safe",
    "smp_roll_std_168h_safe",
    "smp_roll_mean_720h_safe",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "month_sin",
    "month_cos",
    "doy_sin",
    "doy_cos",
    "is_weekend",
    "is_holiday",
    "is_peak_hour",
    "is_solar_hour",
    "scarcity_proxy",
]

OPTIONAL_MODEL_FEATURES = [
    # Design document F2~F7 optional exogenous features. These are populated
    # from an external feature file when available and remain NaN otherwise.
    "demand_forecast_d1",
    "demand_lag_168h",
    "peak_flag",
    "pv_forecast_total",
    "wind_forecast_total",
    "netload",
    "netload_ramp",
    "lng_heat_price",
    "brent_lag_10d",
    "usdkrw_lag_1d",
    "coal_index_lag",
    "temp_pop_weighted",
    "hdd",
    "cdd",
    "irradiance_avg",
    "wind_speed_avg",
    "maintenance_capacity",
    "interconnector_flag",
    "holiday_bridge_flag",
    "seollal_chuseok_window",
]

FEATURES = BASE_FEATURES + OPTIONAL_MODEL_FEATURES


def active_features(df: pd.DataFrame) -> list[str]:
    """Return model inputs, excluding optional columns that are entirely empty."""
    cols = list(BASE_FEATURES)
    for col in OPTIONAL_MODEL_FEATURES:
        if col in df.columns and df[col].notna().any():
            cols.append(col)
    return cols


def split_periods(region_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    usable = region_df.dropna(subset=["smp", "smp_lag_672h", "same_hour_ma_30d_safe"]).copy()
    train = usable[(usable["target_date"] >= TRAIN_START) & (usable["target_date"] < VALID_START)]
    valid = usable[(usable["target_date"] >= VALID_START) & (usable["target_date"] < TEST_START)]
    test = usable[(usable["target_date"] >= TEST_START) & (usable["target_date"] < TEST_END_EXCL)]
    return train, valid, test


def base_pred_frame(df: pd.DataFrame, model_id: str, model_name: str, p50: np.ndarray) -> pd.DataFrame:
    out = df[
        ["region", "target_date", "hour_end", "hour_index_0_23", "target_ts_end", "smp"]
    ].copy()
    out.rename(columns={"smp": "actual"}, inplace=True)
    out["model_id"] = model_id
    out["model_name"] = model_name
    out["p50"] = p50
    return out


def add_residual_quantiles(pred: pd.DataFrame, residuals: np.ndarray) -> pd.DataFrame:
    residuals = np.asarray(residuals, dtype=float)
    residuals = residuals[np.isfinite(residuals)]
    if len(residuals) < 24:
        residuals = np.array([0.0])
    for q in QUANTILES:
        col = f"p{int(q * 100):02d}"
        if col == "p50":
            continue
        pred[col] = pred["p50"] + float(np.quantile(residuals, q))
    pred["p50"] = pred["p50"].astype(float)
    q_cols = ["p10", "p25", "p50", "p75", "p90"]
    pred[q_cols] = np.sort(pred[q_cols].to_numpy(dtype=float), axis=1)
    return pred


def mdl01(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> ModelResult:
    def p50(df: pd.DataFrame) -> np.ndarray:
        return (0.5 * df["smp_lag_168h"] + 0.5 * df["same_hour_ma_7d_safe"]).to_numpy()

    train_p50 = p50(train)
    residuals = train["smp"].to_numpy() - train_p50
    val = base_pred_frame(valid, "MDL-01", "Seasonal naive", p50(valid))
    tst = base_pred_frame(test, "MDL-01", "Seasonal naive", p50(test))
    return ModelResult(
        "MDL-01",
        "Seasonal naive",
        add_residual_quantiles(val, residuals),
        add_residual_quantiles(tst, residuals),
    )


def fit_predict_sklearn(
    train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame, model
) -> tuple[np.ndarray, np.ndarray, np.ndarray, object]:
    feature_cols = active_features(train)
    x_train = train[feature_cols]
    y_train = train["smp"]
    x_valid = valid[feature_cols]
    x_test = test[feature_cols]
    model.fit(x_train, y_train)
    return model.predict(x_train), model.predict(x_valid), model.predict(x_test), model


def mdl02(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> ModelResult:
    pipe = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("ridge", Ridge(alpha=10.0)),
        ]
    )
    tr_pred, val_pred, test_pred, _ = fit_predict_sklearn(train, valid, test, pipe)
    residuals = train["smp"].to_numpy() - tr_pred
    val = base_pred_frame(valid, "MDL-02", "SARIMAX proxy: ridge ARX", val_pred)
    tst = base_pred_frame(test, "MDL-02", "SARIMAX proxy: ridge ARX", test_pred)
    return ModelResult(
        "MDL-02",
        "SARIMAX proxy: ridge ARX",
        add_residual_quantiles(val, residuals),
        add_residual_quantiles(tst, residuals),
    )


def mdl03(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> ModelResult:
    curve_feature = (
        "netload"
        if "netload" in train.columns and train["netload"].notna().sum() >= 24 * 30
        else "scarcity_proxy"
    )
    x_train = train[curve_feature].to_numpy(dtype=float)
    median_x = float(np.nanmedian(x_train))
    x_train = np.nan_to_num(x_train, nan=median_x)
    model = IsotonicRegression(out_of_bounds="clip")
    model.fit(x_train, train["smp"].to_numpy(dtype=float))

    def pred(df: pd.DataFrame) -> np.ndarray:
        x = np.nan_to_num(df[curve_feature].to_numpy(dtype=float), nan=median_x)
        return model.predict(x)

    tr_pred = pred(train)
    residuals = train["smp"].to_numpy() - tr_pred
    label = "Fundamental netload isotonic curve" if curve_feature == "netload" else "Fundamental proxy: isotonic curve"
    val = base_pred_frame(valid, "MDL-03", label, pred(valid))
    tst = base_pred_frame(test, "MDL-03", label, pred(test))
    return ModelResult(
        "MDL-03",
        label,
        add_residual_quantiles(val, residuals),
        add_residual_quantiles(tst, residuals),
    )


def lgbm_point_model(seed: int = 42):
    if HAS_LIGHTGBM:
        return LGBMRegressor(
            objective="mae",
            n_estimators=260,
            learning_rate=0.035,
            num_leaves=39,
            min_child_samples=35,
            subsample=0.85,
            colsample_bytree=0.90,
            reg_lambda=1.0,
            random_state=seed,
            verbosity=-1,
            n_jobs=1,
        )
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("hgb", HistGradientBoostingRegressor(loss="absolute_error", max_iter=260, random_state=seed)),
        ]
    )


def mdl04(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> ModelResult:
    model = lgbm_point_model()
    tr_pred, val_pred, test_pred, _ = fit_predict_sklearn(train, valid, test, model)
    residuals = train["smp"].to_numpy() - tr_pred
    val = base_pred_frame(valid, "MDL-04", "LightGBM point", val_pred)
    tst = base_pred_frame(test, "MDL-04", "LightGBM point", test_pred)
    return ModelResult(
        "MDL-04",
        "LightGBM point",
        add_residual_quantiles(val, residuals),
        add_residual_quantiles(tst, residuals),
    )


def mdl05(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> ModelResult:
    val = valid[
        ["region", "target_date", "hour_end", "hour_index_0_23", "target_ts_end", "smp"]
    ].copy()
    tst = test[
        ["region", "target_date", "hour_end", "hour_index_0_23", "target_ts_end", "smp"]
    ].copy()
    val.rename(columns={"smp": "actual"}, inplace=True)
    tst.rename(columns={"smp": "actual"}, inplace=True)
    val["model_id"] = "MDL-05"
    tst["model_id"] = "MDL-05"
    val["model_name"] = "LightGBM quantile"
    tst["model_name"] = "LightGBM quantile"

    for q in QUANTILES:
        col = f"p{int(q * 100):02d}"
        if HAS_LIGHTGBM:
            model = LGBMRegressor(
                objective="quantile",
                alpha=q,
                n_estimators=260,
                learning_rate=0.035,
                num_leaves=39,
                min_child_samples=35,
                subsample=0.85,
                colsample_bytree=0.90,
                reg_lambda=1.0,
                random_state=420 + int(q * 100),
                verbosity=-1,
                n_jobs=1,
            )
            feature_cols = active_features(train)
            model.fit(train[feature_cols], train["smp"])
            val[col] = model.predict(valid[feature_cols])
            tst[col] = model.predict(test[feature_cols])
        else:
            model = Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    (
                        "hgb",
                        HistGradientBoostingRegressor(
                            loss="quantile",
                            quantile=q,
                            max_iter=260,
                            random_state=420 + int(q * 100),
                        ),
                    ),
                ]
            )
            feature_cols = active_features(train)
            model.fit(train[feature_cols], train["smp"])
            val[col] = model.predict(valid[feature_cols])
            tst[col] = model.predict(test[feature_cols])

    q_cols = ["p10", "p25", "p50", "p75", "p90"]
    val[q_cols] = np.sort(val[q_cols].to_numpy(dtype=float), axis=1)
    tst[q_cols] = np.sort(tst[q_cols].to_numpy(dtype=float), axis=1)
    return ModelResult("MDL-05", "LightGBM quantile", val, tst)


def mdl06(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> ModelResult:
    pipe = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "mlp",
                MLPRegressor(
                    hidden_layer_sizes=(64, 32),
                    activation="relu",
                    alpha=0.002,
                    learning_rate_init=0.001,
                    max_iter=260,
                    early_stopping=True,
                    n_iter_no_change=20,
                    validation_fraction=0.15,
                    random_state=7,
                ),
            ),
        ]
    )
    tr_pred, val_pred, test_pred, _ = fit_predict_sklearn(train, valid, test, pipe)
    residuals = train["smp"].to_numpy() - tr_pred
    val = base_pred_frame(valid, "MDL-06", "TFT proxy: sequence MLP", val_pred)
    tst = base_pred_frame(test, "MDL-06", "TFT proxy: sequence MLP", test_pred)
    return ModelResult(
        "MDL-06",
        "TFT proxy: sequence MLP",
        add_residual_quantiles(val, residuals),
        add_residual_quantiles(tst, residuals),
    )


def load_research_smp_profile() -> pd.DataFrame:
    if not RESEARCH_PROFILE_PATH.exists():
        raise FileNotFoundError(f"MDL-08 profile not found: {RESEARCH_PROFILE_PATH}")
    profile = pd.read_csv(RESEARCH_PROFILE_PATH)
    profile["month"] = pd.to_numeric(profile["month"], errors="raise").astype(int)
    profile["hour_end"] = pd.to_numeric(profile["hour_end"], errors="raise").astype(int)
    profile["smp_profile"] = pd.to_numeric(profile["smp_profile"], errors="raise")
    expected = {(month, hour) for month in range(1, 13) for hour in range(1, 25)}
    actual = set(zip(profile["month"], profile["hour_end"]))
    missing = sorted(expected - actual)
    if missing:
        raise ValueError(f"MDL-08 profile is missing month/hour rows: {missing[:5]}")
    return profile[["month", "hour_end", "smp_profile"]]


def research_profile_pred(df: pd.DataFrame, profile: pd.DataFrame) -> np.ndarray:
    key = df[["month", "hour_end"]].copy()
    key["_row"] = np.arange(len(key))
    merged = key.merge(profile, on=["month", "hour_end"], how="left").sort_values("_row")
    if merged["smp_profile"].isna().any():
        missing = merged.loc[merged["smp_profile"].isna(), ["month", "hour_end"]].drop_duplicates()
        raise ValueError(f"MDL-08 profile lookup failed: {missing.head().to_dict(orient='records')}")
    return merged["smp_profile"].to_numpy(dtype=float)


def mdl08(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> ModelResult:
    profile = load_research_smp_profile()
    train_pred = research_profile_pred(train, profile)
    residuals = train["smp"].to_numpy(dtype=float) - train_pred
    val = base_pred_frame(valid, "MDL-08", "Research workbook profile", research_profile_pred(valid, profile))
    tst = base_pred_frame(test, "MDL-08", "Research workbook profile", research_profile_pred(test, profile))
    return ModelResult(
        "MDL-08",
        "Research workbook profile",
        add_residual_quantiles(val, residuals),
        add_residual_quantiles(tst, residuals),
    )


def ensemble_from_results(results: list[ModelResult]) -> ModelResult:
    val_preds = pd.concat([r.val for r in results], ignore_index=True)
    test_preds = pd.concat([r.test for r in results], ignore_index=True)
    weight_rows = []
    for mid, g in val_preds.groupby("model_id"):
        mae = mean_absolute_error(g["actual"], g["p50"])
        weight_rows.append((mid, mae, 1.0 / max(mae, 1e-6)))
    weights = pd.DataFrame(weight_rows, columns=["model_id", "val_mae", "raw_weight"])
    weights["weight"] = weights["raw_weight"] / weights["raw_weight"].sum()

    def combine(preds: pd.DataFrame) -> pd.DataFrame:
        merged = preds.merge(weights[["model_id", "weight"]], on="model_id", how="left")
        q_cols = ["p10", "p25", "p50", "p75", "p90"]
        key_cols = ["region", "target_date", "hour_end", "hour_index_0_23", "target_ts_end"]
        for col in q_cols:
            merged[f"w_{col}"] = merged[col] * merged["weight"]
        out = (
            merged.groupby(key_cols, as_index=False)
            .agg(
                actual=("actual", "first"),
                p10=("w_p10", "sum"),
                p25=("w_p25", "sum"),
                p50=("w_p50", "sum"),
                p75=("w_p75", "sum"),
                p90=("w_p90", "sum"),
            )
            .copy()
        )
        out["model_id"] = "MDL-07"
        out["model_name"] = "Validation-weighted ensemble"
        q = ["p10", "p25", "p50", "p75", "p90"]
        out[q] = np.sort(out[q].to_numpy(dtype=float), axis=1)
        return out

    val = combine(val_preds)
    test = combine(test_preds)
    val.attrs["weights"] = weights.to_dict(orient="records")
    test.attrs["weights"] = weights.to_dict(orient="records")
    return ModelResult("MDL-07", "Validation-weighted ensemble", val, test)


def pinball(actual: np.ndarray, pred: np.ndarray, q: float) -> float:
    err = actual - pred
    return float(np.mean(np.maximum(q * err, (q - 1) * err)))


def compute_metrics(preds: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for (region, mid, mname), g in preds.groupby(["region", "model_id", "model_name"]):
        actual = g["actual"].to_numpy(dtype=float)
        p50 = g["p50"].to_numpy(dtype=float)
        err = p50 - actual
        mape_mask = np.abs(actual) > 1.0
        mape = (
            float(np.mean(np.abs(err[mape_mask]) / np.abs(actual[mape_mask])) * 100)
            if np.any(mape_mask)
            else float("nan")
        )
        smape = float(
            np.mean(2.0 * np.abs(err) / np.maximum(np.abs(actual) + np.abs(p50), 1e-6)) * 100
        )
        daily = []
        for date, dg in g.groupby("target_date"):
            ai = int(dg.loc[dg["actual"].idxmax(), "hour_end"])
            pi = int(dg.loc[dg["p50"].idxmax(), "hour_end"])
            at = int(dg.loc[dg["actual"].idxmin(), "hour_end"])
            pt = int(dg.loc[dg["p50"].idxmin(), "hour_end"])
            daily.append(
                {
                    "peak_hit_exact": ai == pi,
                    "peak_hit_pm1": abs(ai - pi) <= 1,
                    "trough_hit_exact": at == pt,
                    "trough_hit_pm1": abs(at - pt) <= 1,
                    "spread_abs_error": abs(
                        (dg["p50"].max() - dg["p50"].min())
                        - (dg["actual"].max() - dg["actual"].min())
                    ),
                }
            )
        daily_df = pd.DataFrame(daily)
        rows.append(
            {
                "region": region,
                "model_id": mid,
                "model_name": mname,
                "n_hours": len(g),
                "zero_or_near_zero_actual_hours": int(np.sum(~mape_mask)),
                "mae": float(np.mean(np.abs(err))),
                "rmse": float(np.sqrt(np.mean(err**2))),
                "mape_pct_actual_gt_1": mape,
                "smape_pct": smape,
                "bias": float(np.mean(err)),
                "coverage_p10_p90_pct": float(
                    np.mean((actual >= g["p10"].to_numpy()) & (actual <= g["p90"].to_numpy()))
                    * 100
                ),
                "pinball_mean": float(
                    np.mean(
                        [
                            pinball(actual, g[f"p{int(q * 100):02d}"].to_numpy(dtype=float), q)
                            for q in QUANTILES
                        ]
                    )
                ),
                "peak_hit_exact_pct": float(daily_df["peak_hit_exact"].mean() * 100),
                "peak_hit_pm1_pct": float(daily_df["peak_hit_pm1"].mean() * 100),
                "trough_hit_exact_pct": float(daily_df["trough_hit_exact"].mean() * 100),
                "trough_hit_pm1_pct": float(daily_df["trough_hit_pm1"].mean() * 100),
                "spread_mae": float(daily_df["spread_abs_error"].mean()),
            }
        )

    metric_df = pd.DataFrame(rows).sort_values(["region", "mae"]).reset_index(drop=True)

    hour_rows = []
    for (region, mid, hour), g in preds.groupby(["region", "model_id", "hour_end"]):
        err = g["p50"].to_numpy(dtype=float) - g["actual"].to_numpy(dtype=float)
        actual = g["actual"].to_numpy(dtype=float)
        mape_mask = np.abs(actual) > 1.0
        hour_rows.append(
            {
                "region": region,
                "model_id": mid,
                "hour_end": hour,
                "mae": float(np.mean(np.abs(err))),
                "mape_pct_actual_gt_1": (
                    float(np.mean(np.abs(err[mape_mask]) / np.abs(actual[mape_mask])) * 100)
                    if np.any(mape_mask)
                    else float("nan")
                ),
                "bias": float(np.mean(err)),
            }
        )
    hour_df = pd.DataFrame(hour_rows).sort_values(["region", "model_id", "hour_end"])
    return metric_df, hour_df


def markdown_table(df: pd.DataFrame, floatfmt: str = ".3f") -> str:
    if df.empty:
        return "_No rows_"
    cols = list(df.columns)

    def fmt(v: object) -> str:
        if isinstance(v, (float, np.floating)):
            return format(float(v), floatfmt)
        if isinstance(v, (int, np.integer)):
            return str(int(v))
        return str(v)

    rows = [[fmt(v) for v in row] for row in df.to_numpy()]
    widths = [
        max(len(str(col)), *(len(row[i]) for row in rows))
        for i, col in enumerate(cols)
    ]
    header = "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols)) + " |"
    sep = "| " + " | ".join("-" * widths[i] for i in range(len(cols))) + " |"
    body = [
        "| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(cols))) + " |"
        for row in rows
    ]
    return "\n".join([header, sep, *body])


def run_region(region_df: pd.DataFrame) -> tuple[list[ModelResult], pd.DataFrame]:
    train0, valid, test = split_periods(region_df)
    final_train = pd.concat([train0, valid], ignore_index=True)

    # We fit validation models on pre-April data for ensemble weights. Final test
    # models are fit through April, but exposed as one ModelResult for clarity.
    val_results = [
        mdl01(train0, valid, test),
        mdl02(train0, valid, test),
        mdl03(train0, valid, test),
        mdl04(train0, valid, test),
        mdl05(train0, valid, test),
        mdl06(train0, valid, test),
        mdl08(train0, valid, test),
    ]

    test_results = [
        mdl01(final_train, valid, test),
        mdl02(final_train, valid, test),
        mdl03(final_train, valid, test),
        mdl04(final_train, valid, test),
        mdl05(final_train, valid, test),
        mdl06(final_train, valid, test),
        mdl08(final_train, valid, test),
    ]

    combined = []
    for vr, tr in zip(val_results, test_results):
        combined.append(ModelResult(tr.model_id, tr.model_name, vr.val, tr.test))
    ens = ensemble_from_results(combined)
    combined.append(ens)
    weights = pd.DataFrame(ens.test.attrs.get("weights", []))
    if not weights.empty:
        weights["region"] = region_df["region"].iloc[0]
    return combined, weights


def write_report(metrics: pd.DataFrame, weights: pd.DataFrame, notes: dict[str, object]) -> None:
    report = ROOT / "SMP_2026_05_backtest_report.md"
    best_rows = metrics.sort_values(["region", "mae"]).groupby("region").head(3)

    lines = [
        "# SMP 2026년 5월 시간대별 예측 사전검증",
        "",
        "## 데이터와 전제",
        f"- 원천 SMP: EPSIS 시간별SMP AJAX, 육지/제주, {notes['raw_period']}",
        "- 학습: 2023-05-01~2026-04-30, 앙상블 가중치 검증: 2026-04-01~2026-04-30, 테스트: 2026-05-01~2026-05-31",
        "- 시간 표기는 EPSIS 원천과 같은 01~24시 종료시각 기준이며, CSV에는 0~23 보조 인덱스도 포함했습니다.",
        "- D+1 06:00/09:30 발행 누수를 피하려고 전일 같은 시간(lag24)은 사용하지 않고 최소 48시간 이상 과거 lag/rolling 피처만 사용했습니다.",
        "- 공공데이터포털 DS-01 API는 serviceKey가 필요해 수요예측 입력(F2)은 이번 실행에서 제외했습니다. 공개 파일데이터 수요/태양광은 샘플 데이터라 3년 학습 피처로 쓰지 않았습니다.",
        "",
        "## 모델 구현",
        "- MDL-01: 설계서 계절 나이브 공식(0.5*lag168 + 0.5*최근 7일 동시간 평균).",
        "- MDL-02: SARIMAX 사전검증 대체로 lag/calendar 기반 Ridge ARX.",
        "- MDL-03: 순수요/연료가 미확보로 scarcity proxy 기반 isotonic 공급곡선 대체.",
        "- MDL-04: LightGBM MAE 점예측.",
        "- MDL-05: LightGBM quantile 5개 헤드(P10/P25/P50/P75/P90).",
        "- MDL-06: TFT 사전검증 대체로 lag sequence MLP.",
        "- MDL-07: 2026년 4월 검증 MAE 역수 가중 앙상블. MDL-08도 가중 후보에 포함합니다.",
        "- MDL-08: Research/SMP Model xlsb의 `기준연도SMP` 월별 24시간 프로파일을 대상일 월/시간에 매핑한 외부 연구모델 벤치마크.",
        "",
        "## 상위 결과(MAE 기준)",
        markdown_table(
            best_rows[
                [
                    "region",
                    "model_id",
                    "model_name",
                "mae",
                "mape_pct_actual_gt_1",
                "smape_pct",
                "rmse",
                "coverage_p10_p90_pct",
                    "peak_hit_pm1_pct",
                    "spread_mae",
                ]
            ],
            floatfmt=".3f",
        ),
        "",
        "## 앙상블 가중치",
    ]
    if weights.empty:
        lines.append("- 가중치 정보 없음")
    else:
        lines.append(
            markdown_table(
                weights[["region", "model_id", "val_mae", "weight"]].sort_values(
                    ["region", "weight"], ascending=[True, False]
                ),
                floatfmt=".4f",
            )
        )
    lines.extend(
        [
            "",
            "## 산출 파일",
            "- `processed/smp_hourly_actuals_20230501_20260531.csv`",
            "- `processed/model_predictions_2026_05.csv`",
            "- `processed/model_metrics_2026_05.csv`",
            "- `processed/hourly_metrics_2026_05.csv`",
            "- `SMP_2026_05_backtest.xlsx`",
        ]
    )
    report.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    warnings.filterwarnings("ignore")
    land_path = RAW / "epsis_smp_land_20230501_20260531.js"
    jeju_path = RAW / "epsis_smp_jeju_20230501_20260531.js"
    actuals = pd.concat(
        [parse_epsis_js(land_path, "LAND"), parse_epsis_js(jeju_path, "JEJU")],
        ignore_index=True,
    )
    actuals = actuals.sort_values(["region", "target_date", "hour_end"]).reset_index(drop=True)
    actuals.to_csv(OUT / "smp_hourly_actuals_20230501_20260531.csv", index=False, encoding="utf-8-sig")

    features = build_features(actuals)
    features.to_csv(OUT / "smp_feature_frame_20230501_20260531.csv", index=False, encoding="utf-8-sig")

    all_results: list[ModelResult] = []
    weight_frames: list[pd.DataFrame] = []
    for region, region_df in features.groupby("region", sort=False):
        results, weights = run_region(region_df)
        all_results.extend(results)
        if not weights.empty:
            weight_frames.append(weights)

    preds = pd.concat([r.test for r in all_results], ignore_index=True)
    preds["error"] = preds["p50"] - preds["actual"]
    preds["abs_error"] = preds["error"].abs()
    preds = preds.sort_values(["region", "model_id", "target_date", "hour_end"])
    preds.to_csv(OUT / "model_predictions_2026_05.csv", index=False, encoding="utf-8-sig")

    metrics, hourly = compute_metrics(preds)
    metrics.to_csv(OUT / "model_metrics_2026_05.csv", index=False, encoding="utf-8-sig")
    hourly.to_csv(OUT / "hourly_metrics_2026_05.csv", index=False, encoding="utf-8-sig")

    weights = pd.concat(weight_frames, ignore_index=True) if weight_frames else pd.DataFrame()
    if not weights.empty:
        weights.to_csv(OUT / "ensemble_weights_from_2026_04.csv", index=False, encoding="utf-8-sig")

    notes = {
        "raw_period": "2023-05-01~2026-05-31",
        "lightgbm_available": HAS_LIGHTGBM,
        "n_actual_rows": int(len(actuals)),
        "regions": sorted(actuals["region"].unique().tolist()),
        "mdl08_profile": str(RESEARCH_PROFILE_PATH),
        "source_urls": {
            "epsis_hourly_smp": "https://epsis.kpx.or.kr/epsisnew/selectEkmaSmpShdChart.do",
            "data_go_smp_demand_api": "https://www.data.go.kr/data/15131225/openapi.do",
        },
        "limitations": [
            "DS-01 demand forecast API requires a public-data serviceKey.",
            "Public demand and PPA solar file downloads are samples, not 3-year training inputs.",
            "MDL-03 and MDL-06 are prototype proxies because netload/fuel and TFT runtime are unavailable.",
        ],
    }
    (OUT / "validation_summary.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    xlsx = ROOT / "SMP_2026_05_backtest.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        metrics.to_excel(writer, sheet_name="metrics", index=False)
        hourly.to_excel(writer, sheet_name="hourly_metrics", index=False)
        preds.to_excel(writer, sheet_name="predictions", index=False)
        if not weights.empty:
            weights.to_excel(writer, sheet_name="ensemble_weights", index=False)
    write_report(metrics, weights, notes)

    print(json.dumps({"metrics": str(OUT / "model_metrics_2026_05.csv"), "xlsx": str(xlsx)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
