# SMP 2026년 05월~2026년 06월 13일 시간대별 예측 사전검증

## 데이터와 전제
- 원천 SMP: EPSIS 시간별SMP AJAX, 육지/제주, 2021-06-15~2026-06-13
- 학습: 2021-06-15~2026-03-31, 앙상블 가중치 검증: 2026-04-01~2026-04-30, 테스트: 2026-05-01~2026-06-13
- 시간 표기는 EPSIS 원천과 같은 01~24시 종료시각 기준이며, CSV에는 0~23 보조 인덱스도 포함했습니다.
- D+1 06:00/09:30 발행 누수를 피하려고 전일 같은 시간(lag24)은 사용하지 않고 최소 48시간 이상 과거 lag/rolling 피처만 사용했습니다.
- 외부 피처는 `external_features.csv`에서 병합했습니다. EPSIS 현재부하 기반 수요, Open-Meteo 기상, 일사량 기반 태양광 proxy를 포함합니다.

## 모델 구현
- MDL-01: 설계서 계절 나이브 공식(0.5*lag168 + 0.5*최근 7일 동시간 평균).
- MDL-02: SARIMAX 사전검증 대체로 lag/calendar 기반 Ridge ARX.
- MDL-03: 순수요/연료가 미확보로 scarcity proxy 기반 isotonic 공급곡선 대체.
- MDL-04: LightGBM MAE 점예측.
- MDL-05: LightGBM quantile 5개 헤드(P10/P25/P50/P75/P90).
- MDL-06: TFT 사전검증 대체로 lag sequence MLP.
- MDL-07: 검증 기간 MAE 역수 가중 앙상블.
- MDL-08: 연구 프로파일 파일이 있을 때만 외부 연구모델 벤치마크로 포함합니다.

## 상위 결과(MAE 기준)
| region | model_id | model_name                   | mae    | mape_pct_actual_gt_1 | smape_pct | rmse   | coverage_p10_p90_pct | peak_hit_pm1_pct | spread_mae |
| ------ | -------- | ---------------------------- | ------ | -------------------- | --------- | ------ | -------------------- | ---------------- | ---------- |
| JEJU   | MDL-04   | LightGBM point               | 12.649 | 10.568               | 13.710    | 21.294 | 85.606               | 52.273           | 28.354     |
| JEJU   | MDL-05   | LightGBM quantile            | 12.668 | 10.606               | 13.667    | 21.627 | 66.572               | 54.545           | 28.624     |
| JEJU   | MDL-07   | Validation-weighted ensemble | 12.907 | 11.176               | 14.312    | 20.522 | 91.098               | 54.545           | 26.029     |
| LAND   | MDL-05   | LightGBM quantile            | 9.888  | 10.528               | 8.690     | 13.520 | 67.045               | 36.364           | 13.669     |
| LAND   | MDL-04   | LightGBM point               | 9.964  | 10.588               | 8.749     | 13.593 | 67.330               | 29.545           | 13.721     |
| LAND   | MDL-07   | Validation-weighted ensemble | 9.977  | 10.617               | 8.800     | 13.370 | 84.091               | 43.182           | 14.464     |

## 앙상블 가중치
| region | model_id | val_mae | weight |
| ------ | -------- | ------- | ------ |
| JEJU   | MDL-05   | 9.1501  | 0.2135 |
| JEJU   | MDL-04   | 9.2254  | 0.2117 |
| JEJU   | MDL-01   | 11.1672 | 0.1749 |
| JEJU   | MDL-02   | 11.3462 | 0.1722 |
| JEJU   | MDL-06   | 11.7685 | 0.1660 |
| JEJU   | MDL-03   | 31.6493 | 0.0617 |
| LAND   | MDL-04   | 7.3769  | 0.2056 |
| LAND   | MDL-05   | 7.4701  | 0.2031 |
| LAND   | MDL-06   | 8.8558  | 0.1713 |
| LAND   | MDL-01   | 9.0574  | 0.1675 |
| LAND   | MDL-02   | 9.5629  | 0.1586 |
| LAND   | MDL-03   | 16.1565 | 0.0939 |

## 산출 파일
- `/home/opc/smp/repo/processed/smp_hourly_actuals_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/smp_feature_frame_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/model_predictions_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/model_metrics_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/hourly_metrics_2026_05_2026_06_13.csv`
- `/home/opc/smp/data/SMP_2026_05_06_backtest.xlsx`