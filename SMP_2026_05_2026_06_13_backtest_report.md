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
- MDL-08: MDL-05 분위수 모델에 재생에너지/부하 기반 0원·음수 가격 regime 보정을 추가한 모델.

## 상위 결과(MAE 기준)
| region | model_id | model_name                   | mae    | mape_pct_actual_gt_1 | smape_pct | rmse   | coverage_p10_p90_pct | peak_hit_pm1_pct | spread_mae |
| ------ | -------- | ---------------------------- | ------ | -------------------- | --------- | ------ | -------------------- | ---------------- | ---------- |
| JEJU   | MDL-05   | LightGBM quantile            | 12.576 | 10.556               | 13.674    | 21.121 | 66.572               | 52.273           | 27.843     |
| JEJU   | MDL-04   | LightGBM point               | 12.658 | 10.615               | 13.689    | 21.399 | 85.417               | 52.273           | 28.467     |
| JEJU   | MDL-07   | Validation-weighted ensemble | 12.821 | 11.075               | 14.452    | 19.856 | 91.004               | 54.545           | 24.981     |
| LAND   | MDL-04   | LightGBM point               | 9.908  | 10.508               | 8.699     | 13.516 | 66.761               | 31.818           | 13.637     |
| LAND   | MDL-05   | LightGBM quantile            | 9.962  | 10.538               | 8.743     | 13.505 | 66.951               | 38.636           | 13.988     |
| LAND   | MDL-07   | Validation-weighted ensemble | 10.106 | 10.561               | 8.899     | 13.296 | 82.576               | 40.909           | 14.552     |

## 앙상블 가중치
| region | model_id | val_mae | weight |
| ------ | -------- | ------- | ------ |
| JEJU   | MDL-04   | 9.1292  | 0.1875 |
| JEJU   | MDL-05   | 9.4090  | 0.1819 |
| JEJU   | MDL-01   | 11.1672 | 0.1533 |
| JEJU   | MDL-02   | 11.2861 | 0.1517 |
| JEJU   | MDL-06   | 11.6888 | 0.1464 |
| JEJU   | MDL-08   | 13.6881 | 0.1251 |
| JEJU   | MDL-03   | 31.6493 | 0.0541 |
| LAND   | MDL-05   | 7.2815  | 0.1835 |
| LAND   | MDL-04   | 7.4800  | 0.1787 |
| LAND   | MDL-06   | 7.9608  | 0.1679 |
| LAND   | MDL-01   | 9.0574  | 0.1476 |
| LAND   | MDL-02   | 9.5791  | 0.1395 |
| LAND   | MDL-08   | 13.3495 | 0.1001 |
| LAND   | MDL-03   | 16.1565 | 0.0827 |

## 산출 파일
- `/home/opc/smp/repo/processed/smp_hourly_actuals_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/smp_feature_frame_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/model_predictions_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/model_metrics_2026_05_2026_06_13.csv`
- `/home/opc/smp/repo/processed/hourly_metrics_2026_05_2026_06_13.csv`
- `/home/opc/smp/data/SMP_2026_05_06_backtest.xlsx`