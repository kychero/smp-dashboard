# SMP 예측 대시보드

내일 SMP(계통한계가격)를 여러 모델로 예측·비교하는 화면. 기존 SMP 예측 툴의 산출물(`all_model_forecasts`, 백테스트 `metrics`)을 그대로 읽어 보여준다.

화면 구성: 시간대별 8개 모델 예측 곡선 + 앙상블 + 모델 간 분산 밴드 + 시간대(심야/피크) 리본, 모델 패널(내일 평균·MAPE), 모델×시간 히트맵, 백테스트 MAPE 비교. 지역(제주/육지) 전환 지원.

## 폴더 구조
```
.
├─ web/
│  ├─ index.html              # 대시보드 화면
│  └─ data.js                 # 표시 데이터(빌드 산출물, window.SMP_DATA)
├─ build_dashboard_data.py    # 예측/백테스트 xlsx → web/data.js
├─ agent_config.example.json  # 설정 템플릿(비밀값 제거)
├─ .github/workflows/pages.yml# GitHub Pages 배포
├─ .gitignore
└─ GITHUB.md                  # GitHub 업로드 방법
```

## 바로 보기 (로컬)
정적 파일이라 서버가 필요 없다. 가장 간단한 방법:
```bash
cd web
python -m http.server 8080
# 브라우저: http://localhost:8080
```
> `index.html` 더블클릭으로도 열리지만, 그래프 라이브러리(ECharts)·폰트는 인터넷이 필요하다.

## 데이터 갱신 (툴과 연결)
매일 예측이 끝나면 빌드 스크립트로 `web/data.js`만 다시 만든다:
```bash
python build_dashboard_data.py \
  --forecast forecasts/SMP_forecast_2026-06-12_issue0610.xlsx \
  --backtest SMP_2026_05_backtest.xlsx \
  --out web/data.js
```
- `--forecast` : 예측 엑셀(`all_model_forecasts`·`summary` 시트 사용)
- `--backtest` : 백테스트 엑셀(`metrics` 시트의 MAPE/RMSE/MAE)
- `--champion`/`--ensemble` : 강조할 모델 ID(기본 MDL-04 / MDL-07)

`daily_smp_agent.py`(또는 `run_daily_agent.ps1`)의 마지막에 위 명령을 추가하면, 예측→대시보드 데이터까지 자동 갱신된다. GitHub Pages로 공개 중이라면 갱신된 `web/data.js`를 커밋·푸시하면 화면이 업데이트된다.

## 데이터 형식 (`window.SMP_DATA`)
```
{
  meta: { target_date, issue_ts, champion_id, ensemble_id, unit, backtest_period, generated_at },
  regions: {
    JEJU: { hours:[1..24], models:[ {id,name_ko,name,role,p50[],p10[],p90[],avg,mape,mae,rmse,smape}, ... ] },
    LAND: { ... }
  }
}
```
모델 개수에 상관없이(7개든 8개든) 자동으로 렌더링한다. `role`이 `champion`이면 뱃지, `ensemble`이면 굵은 골드선으로 강조.

## 주의
- 비밀값(`agent_config.json`의 웹훅·SharePoint 링크)과 대용량 Windows 바이너리(`.pydeps/`), 로그·원천데이터(`raw/`)는 커밋하지 않는다(`.gitignore` 참조).
- `web/data.js`에는 가격·지표·모델명만 들어가며 비밀값은 없다 → 커밋 대상.
