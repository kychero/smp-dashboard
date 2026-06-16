# SMP/VPP 운영 아키텍처

Last updated: 2026-06-16

## 1. 현재 운영 상태

이 시스템은 익일 24시간 SMP를 `LAND`, `JEJU` 지역별로 예측하고, 결과를 GitHub Pages 정적 대시보드와 서버 내부 PostgreSQL 백엔드에 동시에 누적한다.

현재 운영 방식은 다음 두 축을 병행한다.

- 정적 대시보드: `web/data.js`를 GitHub Pages로 배포한다.
- 백엔드/이력 저장: 같은 서버의 PostgreSQL 16과 FastAPI를 내부용으로 운영한다.

운영 서버 기준 경로:

| 구분 | 경로/서비스 |
| --- | --- |
| 운영 루트 | `/home/opc/smp` |
| 코드 저장소 | `/home/opc/smp/repo` |
| 일일 배치 | `/home/opc/smp/run_daily.sh` |
| PostgreSQL service | `postgresql.service` |
| FastAPI service | `smp-api.service` |
| 내부 API | `http://127.0.0.1:8000` |
| 공개 대시보드 | `https://kychero.github.io/smp-dashboard/` |

## 2. 전체 구조

```text
systemd timer: smp-daily.timer
  |
  v
/home/opc/smp/run_daily.sh
  |
  +-- git pull
  |
  +-- collect_external_features.py
  |     +-- Open-Meteo weather
  |     +-- EPSIS load proxy
  |     +-- PV proxy
  |     +-- /home/opc/smp/data/external_features.csv
  |
  +-- daily_smp_agent.py
  |     +-- EPSIS SMP actual refresh
  |     +-- model training and D+1 forecast
  |     +-- /home/opc/smp/data/daily_outputs/SMP_forecast_*.csv/xlsx
  |
  +-- score_forecast_history.py
  |     +-- forecast vs actual scoring
  |     +-- /home/opc/smp/data/forecast_score_history.csv
  |
  +-- build_revenue_history.py
  |     +-- ESS/PV/CP/imbalance revenue estimates
  |     +-- /home/opc/smp/data/revenue_history.csv
  |
  +-- python -m api.ingest_files
  |     +-- PostgreSQL vpp.smp_actual
  |     +-- PostgreSQL vpp.smp_forecast
  |     +-- PostgreSQL vpp.forecast_score
  |     +-- PostgreSQL vpp.revenue_history
  |
  +-- build_dashboard_data.py
  |     +-- web/data.js
  |
  +-- git commit && git push
        +-- GitHub Pages update
```

## 3. 일일 배치 흐름

`smp-daily.timer`는 매일 `21:05 UTC`에 `smp-daily.service`를 실행한다. 이는 KST 기준 `06:05`이다.

`run_daily.sh`의 주요 동작:

1. 저장소를 `git pull --ff-only origin main`으로 최신화한다.
2. KST 기준 내일을 기본 예측 대상일로 계산한다.
3. 기존 산출물 중 최신 예측일 다음 날짜부터 내일까지 누락 날짜를 자동 백필한다.
4. 외생 피처를 최근 구간 중심으로 갱신한다.
5. 날짜별로 `daily_smp_agent.py --target-date YYYY-MM-DD`를 실행한다.
6. 과거 예측 CSV와 EPSIS 실측 캐시를 매칭해 예측 점수 이력을 갱신한다.
7. 실측/예측 SMP 기반 ESS/VPP 수익 이력을 생성한다.
8. `api/.env`가 있으면 PostgreSQL에 산출물을 적재한다.
9. 최신 예측 Excel, 백테스트, 점수, 수익, 실측 이력을 묶어 `web/data.js`를 재생성한다.
10. `web/data.js` 등 변경 파일을 커밋/푸시한다.

수동 백필:

```bash
RUN_TARGET_DATES="2026-06-15 2026-06-16 2026-06-17" /home/opc/smp/run_daily.sh
```

## 4. 데이터 저장 계층

### 4.1 파일 계층

파일 계층은 GitHub Pages 대시보드와 운영 백업의 기준이다.

| 파일/디렉터리 | 역할 |
| --- | --- |
| `/home/opc/smp/data/external_features.csv` | 외생 피처 누적 파일 |
| `/home/opc/smp/repo/processed/smp_actuals_cache.csv` | EPSIS SMP 실측 누적 캐시 |
| `/home/opc/smp/data/daily_outputs/` | 일별 예측 CSV/XLSX 산출물 |
| `/home/opc/smp/data/forecast_score_history.csv` | 일별 예측 점수 이력 |
| `/home/opc/smp/data/revenue_history.csv` | 실측/예측 기반 수익 이력 |
| `/home/opc/smp/repo/web/data.js` | 정적 대시보드 payload |

### 4.2 PostgreSQL 계층

DB는 백엔드/API/장기 이력 저장용이다. 정적 대시보드는 DB가 장애여도 파일 기반으로 계속 동작한다.

운영 DB:

```text
PostgreSQL 16
database: smp
user: smp
host: 127.0.0.1
port: 5432
DATABASE_URL=postgresql://smp:smp@127.0.0.1:5432/smp
```

현재 주요 적재 테이블:

| 테이블 | 설명 |
| --- | --- |
| `vpp.smp_actual` | EPSIS 실측 SMP. `region,target_date,hour_end` 기준 upsert |
| `vpp.smp_forecast` | 모델별 시간대 예측. `region,target_date,hour_end,model_id` 기준 upsert |
| `vpp.forecast_score` | 실제값이 확인된 예측의 모델별 성능 점수 |
| `vpp.revenue_history` | 파일 기반으로 계산한 실측/예측 수익 이력 |
| `vpp.revenue_run` | API를 통한 수익 시뮬레이션 저장 결과 |
| `vpp.resource` | 향후 PV/WIND/ESS/HYBRID 자원 관리용 |
| `vpp.settlement` | 향후 정산 결과 저장용 |

2026-06-16 세팅 직후 적재 상태:

| 테이블 | row count |
| --- | ---: |
| `vpp.smp_actual` | 87,672 |
| `vpp.smp_forecast` | 1,152 |
| `vpp.forecast_score` | 16 |
| `vpp.revenue_history` | 3,701 |

최신 실측일은 `2026-06-15`이고, 예측은 `2026-06-15`, `2026-06-16`, `2026-06-17`이 적재되어 있다.

## 5. DB 스키마

스키마 파일:

| 파일 | 역할 |
| --- | --- |
| `db/001_init.sql` | 표준 PostgreSQL 스키마 |
| `db/002_timescaledb_optional.sql` | TimescaleDB가 설치된 경우 선택 적용 |
| `db/003_revenue_history.sql` | 배치 산출 수익 이력 테이블 |

현재 서버는 TimescaleDB 없이 표준 PostgreSQL 16으로 운영한다.

핵심 primary key:

```text
vpp.smp_actual:
  (region, target_date, hour_end)

vpp.smp_forecast:
  (region, target_date, hour_end, model_id)

vpp.forecast_score:
  (target_date, region, model_id)

vpp.revenue_history:
  (target_date, region, source, model_id)
```

모든 적재는 `ON CONFLICT DO UPDATE` 방식이다. 같은 날짜를 재수집하거나 재예측해도 DB에는 최신 값으로 덮어쓴다.

## 6. API

FastAPI는 내부용으로만 실행 중이다.

```text
service: smp-api.service
bind: 127.0.0.1:8000
working directory: /home/opc/smp/repo
environment file: /etc/smp-api.env
```

주요 endpoint:

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/health` | API health check |
| `GET` | `/metadata/effective-capacity` | ESS/PV 월별 실효용량률 reference |
| `GET` | `/forecasts?region=JEJU&target_date=YYYY-MM-DD` | 모델별 시간대 예측 조회 |
| `GET` | `/actuals?region=JEJU&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` | 실측 SMP 조회 |
| `GET` | `/scores?region=JEJU` | 예측 점수 조회 |
| `POST` | `/revenue/estimate` | SMP 기반 수익 추정. `persist=true`면 `vpp.revenue_run` 저장 |

헬스체크:

```bash
curl http://127.0.0.1:8000/health
```

예측 조회:

```bash
curl 'http://127.0.0.1:8000/forecasts?region=JEJU&target_date=2026-06-17'
```

수익 추정:

```bash
curl -X POST http://127.0.0.1:8000/revenue/estimate \
  -H 'Content-Type: application/json' \
  -d '{
    "region":"JEJU",
    "target_date":"2026-06-17",
    "model_id":"MDL-07",
    "scenario":"base",
    "view_mode":"day",
    "pv_capacity_mw":5,
    "ess_energy_mwh":10,
    "ess_power_mw":5,
    "persist":true
  }'
```

## 7. 정적 대시보드

대시보드는 `web/index.html`이 `web/data.js`를 로드하는 구조다.

현재 화면:

| 탭 | 내용 |
| --- | --- |
| 예측 | 지역별 익일 SMP 모델 곡선, KPI, 히트맵, MAPE, 접이식 시간대별 예측값 표 |
| 검증 | 백테스트 실측 대비 모델 예측, 5월 이후 일자별 예측 점수, 점수 표 |
| 수익 추정 | 실측/예측 SMP 기반 PV/ESS/CP/보조금/임밸런스 수익 추정 |
| 정보 | 데이터 출처, 모델, 정산 산식 요약 |

`build_dashboard_data.py`는 다음을 `web/data.js`에 포함한다.

- 최신 예측 모델별 P10/P50/P90
- 백테스트 시계열
- 일일 score history
- revenue history
- 최근 60일 actual history

## 8. 모델 구조

모델 구현은 `run_smp_backtest.py`와 `daily_smp_agent.py`가 공유한다.

| 모델 | 설명 |
| --- | --- |
| `MDL-01` | 계절 나이브 |
| `MDL-02` | SARIMAX proxy: Ridge ARX |
| `MDL-03` | Fundamental netload isotonic curve |
| `MDL-04` | LightGBM point. 현재 champion |
| `MDL-05` | LightGBM quantile |
| `MDL-06` | TFT proxy: sequence MLP |
| `MDL-07` | validation-weighted ensemble. 현재 risk/ensemble |
| `MDL-08` | zero-regime LightGBM quantile |

`MDL-07`은 검증 MAE 역수 기반 가중 평균이다.

```text
raw_weight = 1 / max(validation_mae, 1e-6)
weight = raw_weight / sum(raw_weight)
```

## 9. 수익 계산

수익 계산은 `api/revenue_engine.py`와 `build_revenue_history.py`가 같은 가정을 공유한다.

주요 입력:

- SMP 24시간 시계열
- PV 설비용량
- ESS 에너지/출력
- 충방전 효율
- 보조금 단가
- RCP, RPCF
- 예측 MAPE
- 급전지시 여부

주요 산출:

- PV 시장참여 수익
- PV 보조금
- PV CP
- ESS 차익
- ESS CP
- 임밸런스 차감
- 총 수익

현재 값은 MVP 추정이며 정산 확정값이 아니다.

## 10. 운영 명령

서비스 상태:

```bash
sudo systemctl status postgresql --no-pager -l
sudo systemctl status smp-api --no-pager -l
sudo systemctl list-timers --all | grep smp-daily
```

DB row count:

```bash
psql postgresql://smp:smp@127.0.0.1:5432/smp -c "
select 'smp_actual' as table_name, count(*) from vpp.smp_actual
union all select 'smp_forecast', count(*) from vpp.smp_forecast
union all select 'forecast_score', count(*) from vpp.forecast_score
union all select 'revenue_history', count(*) from vpp.revenue_history
order by table_name;"
```

수동 DB 적재:

```bash
cd /home/opc/smp/repo
/home/opc/smp/venv/bin/python -m api.ingest_files \
  --actuals /home/opc/smp/repo/processed/smp_actuals_cache.csv \
  --forecast-dir /home/opc/smp/data/daily_outputs \
  --scores /home/opc/smp/data/forecast_score_history.csv \
  --revenue-history /home/opc/smp/data/revenue_history.csv
```

수동 배치:

```bash
/home/opc/smp/run_daily.sh
```

수동 날짜 백필:

```bash
RUN_TARGET_DATES="2026-06-16 2026-06-17" /home/opc/smp/run_daily.sh
```

## 11. 보안과 공개 범위

- GitHub Pages는 정적 파일만 공개한다.
- PostgreSQL은 로컬 DB로 운영한다.
- FastAPI는 `127.0.0.1:8000`에만 bind한다.
- `/etc/smp-api.env`와 `api/.env`에는 DB URL이 들어가며 Git에 커밋하지 않는다.
- 외부 API 공개가 필요하면 reverse proxy, TLS, 인증, CORS 제한을 별도로 설계해야 한다.

## 12. 개선 후보

- `vpp.revenue_history` 조회 API 추가
- 백테스트 점수를 DB에도 5월 전체 일자 단위로 저장
- TimescaleDB 확장 설치 후 hypertable 전환
- API 인증 및 외부 공개용 reverse proxy 구성
- 자원별 PV/ESS 포트폴리오 관리와 `vpp.resource` 활용
- 수익 시뮬레이션 저장 결과를 대시보드에서 비교 조회
