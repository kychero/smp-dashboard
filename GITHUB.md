# GitHub에 올리고 공개하기 (GITHUB.md)

이 대시보드를 GitHub에 올리고, **GitHub Pages**로 공개 URL을 만들어 "보여주는" 방법. (Windows 기준)

---

## 0. 올리기 전 보안 체크 (중요)

업로드하신 SMP 툴에는 공개하면 안 되는 것들이 섞여 있습니다. 아래를 먼저 확인하세요.

- [ ] `agent_config.json` — **Power Automate 웹훅 URL**, **SharePoint 링크**가 들어 있음. 커밋 금지. 대신 `agent_config.example.json`(비밀값 제거본)만 올립니다.
- [ ] `.pydeps/` — 90MB대 Windows DLL(lightgbm/numpy). 커밋 금지(용량·OS 종속).
- [ ] `logs/`, `raw/` — 로그·원천데이터. 커밋 금지.
- [ ] `latest_run.json`/엑셀의 `C:\Users\...` 개인 경로·SharePoint 링크 — 공개 저장소엔 두지 않기.

→ 동봉한 `.gitignore`가 위 항목을 자동 제외합니다. 저장소 루트에 `.gitignore`가 있는지 꼭 확인하세요. 공개가 부담되면 **Private 저장소**로 시작하면 됩니다(Pages는 Private도 가능, 단 무료 플랜은 공개 Pages가 기본).

---

## 1. 저장소 만들기

GitHub에서 **New repository** → 이름 예 `smp-dashboard` → Private(권장) → Create.

---

## 2. 올리는 방법 (셋 중 하나)

### 방법 A — GitHub Desktop (Windows에서 가장 쉬움, CLI 불필요)
1. GitHub Desktop 설치 후 로그인.
2. **File ▸ Add local repository** 로 이 폴더(대시보드 + 툴)를 선택.
   - 아직 git 저장소가 아니면 **create a repository here** 안내가 뜸 → 진행.
3. `.gitignore`가 폴더에 있는지 확인(없으면 동봉본 복사).
4. 좌하단에 커밋 메시지 입력 → **Commit to main** → **Publish repository**.
   - "Keep this code private" 체크 권장.
5. 끝. 이후 변경은 **Commit → Push** 반복.

### 방법 B — git 명령어 (Git for Windows 또는 Codespaces 터미널)
```bash
cd "프로젝트폴더"
git init
git branch -M main
# .gitignore가 있는지 먼저 확인! (.pydeps/secrets 제외)
git add .
git status          # .pydeps/ logs/ agent_config.json 이 목록에 없어야 정상
git commit -m "Add SMP forecast dashboard"
git remote add origin https://github.com/<사용자명>/smp-dashboard.git
git push -u origin main
```
> 혹시 `.pydeps`가 이미 add 됐다면: `git rm -r --cached .pydeps && git commit -m "remove binaries"`.

### 방법 C — 웹 드래그 앤 드롭 (소규모일 때)
저장소 페이지 → **Add file ▸ Upload files** → `web/`, `build_dashboard_data.py`, `README.md` 등을 끌어다 놓기 → Commit. (대용량/폴더 구조가 많으면 A·B 권장.)

---

## 3. GitHub Pages로 공개 (화면 보여주기)

동봉한 `.github/workflows/pages.yml`이 `web/` 폴더를 자동 게시합니다.

1. 저장소 **Settings ▸ Pages ▸ Build and deployment ▸ Source** 를 **GitHub Actions** 로 설정.
2. `main`에 푸시하면 워크플로가 돌고, 끝나면 Pages URL이 생깁니다:
   `https://<사용자명>.github.io/smp-dashboard/`
3. **Actions** 탭에서 배포 성공을 확인. 첫 배포는 1–2분 걸립니다.

> 워크플로 없이 하려면: `web` 폴더 이름을 `docs`로 바꾸고, Settings ▸ Pages ▸ "Deploy from a branch" ▸ `main` / `/docs` 선택. (이 경우 `index.html`이 `docs/index.html`이어야 함.)

---

## 4. 매일 자동 갱신 (예측 → 화면 반영)

예측이 끝날 때마다 `web/data.js`만 다시 만들고 푸시하면 공개 화면이 갱신됩니다.

`run_daily_agent.ps1` 끝부분에 추가(경로는 실제에 맞게):
```powershell
python build_dashboard_data.py `
  --forecast "forecasts\SMP_forecast_$(Get-Date -Format yyyy-MM-dd)_issue0610.xlsx" `
  --backtest "SMP_2026_05_backtest.xlsx" `
  --out "web\data.js"

git add web/data.js
git commit -m "data: refresh $(Get-Date -Format yyyy-MM-dd)"
git push
```
> 자동 푸시에는 인증이 필요합니다. GitHub Desktop으로 수동 푸시하거나, PAT(Personal Access Token)/`gh auth login`을 설정하세요. PAT는 코드에 넣지 말고 Windows 자격증명 관리자에 저장합니다.

---

## 5. 확인 체크리스트
- [ ] Pages URL이 열리고 그래프가 보인다(인터넷 연결 필요 — ECharts/폰트 CDN).
- [ ] `git status`에 `.pydeps/`, `logs/`, `agent_config.json`이 안 보인다.
- [ ] 지역 탭(제주/육지) 전환, 모델 토글, 호버 툴팁이 동작한다.
- [ ] 새 예측 후 `web/data.js`를 푸시하면 화면 숫자가 바뀐다.
