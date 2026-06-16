# PostgreSQL/FastAPI 운영 가이드

Last updated: 2026-06-16

## 1. 현재 서버 구성

현재 서버는 파일 기반 GitHub Pages 대시보드를 유지하면서, PostgreSQL과 FastAPI를 백엔드/이력 저장용으로 같이 운영한다.

| 항목 | 값 |
| --- | --- |
| OS | Oracle Linux Server 9.7 |
| DB | PostgreSQL 16.13 |
| DB service | `postgresql.service` |
| API service | `smp-api.service` |
| API bind | `127.0.0.1:8000` |
| DB name | `smp` |
| DB user | `smp` |
| DB URL | `postgresql://smp:smp@127.0.0.1:5432/smp` |
| API env | `/etc/smp-api.env` |
| repo env | `/home/opc/smp/repo/api/.env` |

`api/.env`와 `/etc/smp-api.env`는 Git에 커밋하지 않는다.

## 2. 설치 이력

PostgreSQL 16 모듈을 활성화하고 서버/클라이언트를 설치했다.

```bash
sudo dnf -y module enable postgresql:16
sudo dnf -y install postgresql-server postgresql postgresql-contrib
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

로컬 TCP 접속을 `smp` 계정의 비밀번호 인증으로 허용하기 위해 `/var/lib/pgsql/data/pg_hba.conf`의 local TCP 설정을 다음처럼 조정했다.

```text
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256
```

변경 후:

```bash
sudo systemctl reload postgresql
```

DB와 유저:

```bash
sudo -u postgres psql -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'smp') THEN CREATE ROLE smp LOGIN PASSWORD 'smp'; ELSE ALTER ROLE smp WITH LOGIN PASSWORD 'smp'; END IF; END \$\$;"
sudo -u postgres createdb -O smp smp
```

## 3. 스키마 적용

표준 PostgreSQL 스키마:

```bash
cd /home/opc/smp/repo
psql postgresql://smp:smp@127.0.0.1:5432/smp -v ON_ERROR_STOP=1 -f db/001_init.sql
psql postgresql://smp:smp@127.0.0.1:5432/smp -v ON_ERROR_STOP=1 -f db/003_revenue_history.sql
```

TimescaleDB가 설치된 환경에서는 선택적으로 다음을 적용할 수 있다.

```bash
psql "$DATABASE_URL" -f db/002_timescaledb_optional.sql
```

현재 서버에는 TimescaleDB가 설치되어 있지 않으므로 `002_timescaledb_optional.sql`은 적용하지 않았다.

## 4. API 환경 파일

repo용:

```bash
cat > /home/opc/smp/repo/api/.env <<'EOF'
DATABASE_URL=postgresql://smp:smp@127.0.0.1:5432/smp
VPP_ALLOWED_ORIGINS=http://localhost:8080,https://*.github.io
EOF
chmod 600 /home/opc/smp/repo/api/.env
```

systemd용:

```bash
sudo tee /etc/smp-api.env >/dev/null <<'EOF'
DATABASE_URL=postgresql://smp:smp@127.0.0.1:5432/smp
VPP_ALLOWED_ORIGINS=http://localhost:8080,https://*.github.io
EOF
sudo chmod 640 /etc/smp-api.env
sudo chown root:root /etc/smp-api.env
```

## 5. API 서비스

서비스 파일: `/etc/systemd/system/smp-api.service`

```ini
[Unit]
Description=SMP VPP FastAPI backend
After=network-online.target postgresql.service
Wants=network-online.target
Requires=postgresql.service

[Service]
Type=simple
User=opc
WorkingDirectory=/home/opc/smp/repo
EnvironmentFile=/etc/smp-api.env
ExecStart=/bin/bash -lc 'cd /home/opc/smp/repo && exec /home/opc/smp/venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

적용:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now smp-api
```

확인:

```bash
sudo systemctl status postgresql --no-pager -l
sudo systemctl status smp-api --no-pager -l
curl http://127.0.0.1:8000/health
```

## 6. 데이터 적재

수동 전체 적재:

```bash
cd /home/opc/smp/repo
/home/opc/smp/venv/bin/python -m api.ingest_files \
  --actuals /home/opc/smp/repo/processed/smp_actuals_cache.csv \
  --forecast-dir /home/opc/smp/data/daily_outputs \
  --scores /home/opc/smp/data/forecast_score_history.csv \
  --revenue-history /home/opc/smp/data/revenue_history.csv
```

2026-06-16 초기 적재 결과:

```text
{'actuals': 87672, 'forecasts': 1152, 'scores': 16, 'revenue_history': 3701}
```

`run_daily.sh`에는 DB 적재 단계가 포함되어 있다. `api/.env`가 존재하면 매일 다음 데이터를 PostgreSQL에 upsert한다.

- `vpp.smp_actual`
- `vpp.smp_forecast`
- `vpp.forecast_score`
- `vpp.revenue_history`

## 7. 주요 API

Health:

```bash
curl http://127.0.0.1:8000/health
```

Effective capacity metadata:

```bash
curl http://127.0.0.1:8000/metadata/effective-capacity
```

Forecast:

```bash
curl 'http://127.0.0.1:8000/forecasts?region=JEJU&target_date=2026-06-17'
curl 'http://127.0.0.1:8000/forecasts?region=JEJU&target_date=2026-06-17&model_id=MDL-07'
```

Actuals:

```bash
curl 'http://127.0.0.1:8000/actuals?region=JEJU&start_date=2026-06-01&end_date=2026-06-15'
```

Scores:

```bash
curl 'http://127.0.0.1:8000/scores?region=JEJU'
```

Revenue estimate:

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

`persist=true`이면 결과를 `vpp.revenue_run`에 저장한다.

## 8. 운영 점검

서비스:

```bash
sudo systemctl is-enabled postgresql smp-api
sudo systemctl is-active postgresql smp-api
```

DB 건수:

```bash
psql postgresql://smp:smp@127.0.0.1:5432/smp -c "
select 'smp_actual' as table_name, count(*) from vpp.smp_actual
union all select 'smp_forecast', count(*) from vpp.smp_forecast
union all select 'forecast_score', count(*) from vpp.forecast_score
union all select 'revenue_history', count(*) from vpp.revenue_history
order by table_name;"
```

최신 날짜:

```bash
psql postgresql://smp:smp@127.0.0.1:5432/smp -c "
select max(target_date) as latest_actual from vpp.smp_actual;
select target_date, count(*) from vpp.smp_forecast group by target_date order by target_date;"
```

로그:

```bash
sudo journalctl -u postgresql -n 100 --no-pager
sudo journalctl -u smp-api -n 100 --no-pager
```

## 9. 공개 정책

현재 API는 로컬 전용이다.

- bind: `127.0.0.1:8000`
- 외부 인터넷에서 직접 접근 불가
- GitHub Pages는 여전히 `web/data.js`를 읽는 정적 구조

외부 공개가 필요하면 다음을 먼저 설계해야 한다.

- reverse proxy
- TLS
- 인증/권한
- CORS origin 제한
- rate limit
