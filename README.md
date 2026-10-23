# 미결(未結) — 끝나지 않은 뉴스를 추적하는 뉴스 애프터케어 서비스

> 뉴스는 끝났지만, 문제도 끝났습니까?

2026 뉴스빅데이터 해커톤(한국언론진흥재단) 일반 부문 출품작 · 팀 **후속취재단**

빅카인즈 뉴스·인용문 데이터로 재난·안전 사건 이후 **기관이 공개적으로 밝힌 약속(주체·행동·기한)** 을 구조화하고, 후속 기사 근거로 사건 상태를 다섯 가지 중 하나로 표시하며, **후속보도 공백이 있는 사건을 목록 상단에 먼저** 보여준다.

| 상태 (이 5개뿐) | 뜻 |
|---|---|
| 조치 완료 근거 확인 | 완료형 표현의 후속 기사 근거가 있음 |
| 조치 진행 정황 확인 | 착공·추진 등 진행 근거가 있음 ("완료 예정"은 진행) |
| 새로운 문제 발생 | 같은 시설·사건에서 재발·차질 |
| 후속보도 부족 | 후속 기사가 없거나 오래됐거나, 기한이 지났는데 근거가 없음 |
| 판단 불가 | 후속 기사는 있으나 근거가 없거나 상충, 또는 추적 가능한 약속이 없음 |

> 보도가 없다는 사실은 조치가 없었다는 근거가 아니라, 확인이 필요하다는 신호입니다. — 모든 상태 옆에 고정 노출

## 빠른 실행 (예선 목업, API 키 불필요)

```bash
# 백엔드
cd backend
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python scripts/make_fixtures.py --today 2026-09-11     # 가상 사건 8건 픽스처 (backend/fixtures/ 아래에만 씀)
TODAY_OVERRIDE=2026-09-11 .venv/bin/python scripts/run_pipeline.py --stage all --reset
.venv/bin/uvicorn app.main:app --reload --port 8000

# 프런트 (다른 터미널)
cd frontend
npm install && cp .env.local.example .env.local
npm run dev -- -p 3000        # http://localhost:3000
```

저장소 루트의 `.env.example` 을 `.env` 로 복사해 모드를 정한다. 예선 기본값은 `BIGKINDS_MODE=mock`, `LLM_MODE=stub`, `EMBEDDING_BACKEND=hash`, `TODAY_OVERRIDE=2026-09-11`(픽스처 기준일 고정). 화면의 "파이프라인 실행" API 를 쓰려면 `PIPELINE_API_ENABLED=true`(기본 꺼짐).

**관리자 화면 보호.** 백엔드 `.env` 의 `ADMIN_TOKEN` 을 설정하면 `/api/admin/*`·`/api/pipeline/run` 은 `X-Admin-Token` 이 있어야 한다. 같은 값을 `frontend/.env.local` 의 `ADMIN_TOKEN` 에도 넣는다 — 프런트 **서버**(서버 컴포넌트와 `/api/admin|pipeline` Route Handler 프록시)가 헤더를 붙이고 브라우저에는 토큰이 내려가지 않는다. 따라서 프런트에 접근할 수 있는 사람은 누구나 관리자 화면을 쓸 수 있다. 토큰은 백엔드를 다른 호스트로부터 지키는 용도이고, 프런트는 localhost 나 별도 접근 제어 뒤에 둔다.

**호출량.** 조용한 사건은 사건당 4회(time_line 2 + search 2)로 끝난다. 큰 사건은 초기 2주를 날짜별로 받고, 이후 구간은 조치 상태 질의(완료·준공·착공 …)와 분기별 표본만 받아 월별로 나눠 부르지 않는다(3년 된 중형 사건 ≈ 20회). 스케줄러를 켜기 전에 실 시드로 `run_pipeline.py --stage collect` 를 한 번 돌려 `pipeline_runs.stats.collect[].api_calls` 합계를 확인한다.

**동시 실행.** 파이프라인은 프로세스 Lock 과 파일 Lock(`backend/data/pipeline.lock`)으로 한 번에 하나만 돈다 — 서버와 CLI 를 같이 켜도 겹치지 않고(`409` / `PipelineBusy`), `uvicorn --workers N` 도 막힌다. 다만 관리자 확정·해제 뒤의 규칙 재판정은 Lock 밖에서 바로 실행된다(LLM·API 호출 없음).

시드·정답셋 파일은 모드를 따른다. `mock` 은 `backend/fixtures/{seeds,gold}/events.yaml`(가상 8건 — 테스트도 이것만 쓴다), `record`/`live` 는 `backend/seeds/events.yaml`·`backend/gold/events.yaml`(실사건). `SEEDS_FILE`·`GOLD_FILE` 로 바꿀 수 있다(상대경로는 `backend/` 기준).

```bash
cd backend && .venv/bin/python -m pytest -q                       # 테스트
TODAY_OVERRIDE=2026-09-11 .venv/bin/python scripts/evaluate.py     # 정답셋 대비 평가 -> reports/eval_<날짜>.md
TODAY_OVERRIDE=2026-09-11 .venv/bin/python scripts/evaluate.py --sweep link_auto=0.45,0.55,0.65
```

## 본선 전환 (API 키 발급 후)

1. `.env` 에 `BIGKINDS_MODE=record`, `BIGKINDS_ACCESS_KEY=...`, `TODAY_OVERRIDE=` 비움 — 실 API 응답이 `backend/fixtures/bigkinds/recordings/` 에 저장·재생된다 (PT 오프라인 시연 안전장치).
2. `BIGKINDS_MODE=live .venv/bin/python scripts/smoke_live.py` 로 7개 엔드포인트를 한 번씩 불러 실제 반환 필드(`document_keys`)와 인용문 검색 범위를 확인하고 `docs/ARCHITECTURE.md` "지침서와 실제 응답 차이"에 기록한다. 2026-10-12 첫 호출은 `Invalid Access Key` — 키 활성화를 확인한다.
3. `run_pipeline.py --stage discover --from 2025-01-01 --until 2025-12-31` 처럼 날짜 범위를 주어 `seeds/events.draft.yaml` 초안을 만들고(날짜마다 오늘의 이슈를 조회), 사람이 20~30건을 `seeds/events.yaml` 로 확정한다. 그다음 `run_pipeline.py --stage all --reset` 으로 예선 때 DB 에 쌓인 가상 사건을 지우고 실사건으로 다시 만든다(DB 는 모드와 무관하게 `DATABASE_URL` 하나를 쓴다).
4. `LLM_MODE=live`, `ANTHROPIC_API_KEY=...` (기본 모델 `claude-opus-5`, 비용이 부담되면 `LLM_MODEL=claude-sonnet-5`).
5. `pip install -e ".[embeddings]"` 후 `EMBEDDING_BACKEND=st` (한국어 sentence-transformers).
6. `gold/events.yaml` 에 실제 사건 30건을 라벨링하고(`as_of` 기준일, news_id 는 따옴표) `evaluate.py --sweep` 으로 임계값을 정한다. 스윕은 DB 사본에서 돌아 운영 DB 를 바꾸지 않는다.
7. PT 시연: 녹화가 끝난 날짜를 `TODAY_OVERRIDE` 로 고정하고 `BIGKINDS_REPLAY_ONLY=true` 로 켜면 키·네트워크 없이 같은 화면이 나온다. 녹화 폴더(`backend/fixtures/bigkinds/recordings/`)를 시연 PC 로 복사한다.

## 빅카인즈 API 활용 (지침서 newstore V1.0)

| API | 경로 | 사용 위치 | 활용 방식 |
|---|---|---|---|
| 오늘의 이슈 | `/issue_ranking` | `pipeline/discover.py` | 날짜별 상위 이슈의 `news_cluster` 를 뉴스 상세 조회로 받아 `category_incident` 가 재난·안전 3계열이면 사건 후보 |
| 뉴스 검색 | `/search/news` | `pipeline/collect.py` | 시설·사건명 × 지역·기관 질의. 발생 직후 2주 + 월별 구간으로 나눠 페이징(구간 상한, 잘림 기록), `fields` 명시. 약속 추출 뒤에는 행동 키워드로 후속 기사를 재검색 |
| 뉴스 상세 조회 | `/search/news` (`news_ids`) | `pipeline/discover.py` | 이슈 클러스터 기사의 분류 확인 |
| 연관어 분석 | `/word_cloud` | `pipeline/collect.py` | 사건 핵심어 연관어 3개로 질의 보강 (후속 기사의 다른 표현 대비) |
| 뉴스 인용문 검색 | `/search/quotation` | `pipeline/extract.py` | `source`(발언 주체)·`quotation`(발언 내용) — 약속 구조화의 핵심 데이터원. 30일 창으로 페이징, 사건 연결 기사만 사용 |
| 키워드 트렌드 | `/time_line` | `pipeline/trend.py` | 월별 기사 건수 → 보도량 급감(`drop_ratio`) 정량화 |
| 뉴스 변경이력 | `/changeTracker/news` | `pipeline/tracker.py` | 근거 기사가 `Update`/`Cancelled` 면 재검토 표시, 신호 재분류·재판정, 삭제 기사의 약속 추적 중단 |

데이터 이용 범위: 뉴스 `content` 200자·`hilight` 200/300자 등 API 반환 범위만 저장하며 원문은 `provider_link_page` 외부 링크로만 연결한다. 언론사 원문 크롤링 코드는 없다. 공공기관 공식 게시판 확인(`app/notices/`, v2 §3)은 뉴스가 아닌 기관 발표를 트리거 조건 아래에서만 읽는 별도 기능이다(`docs/NOTICES.md`).

## 구조

```
backend/app/bigkinds   요청·응답 모델(지침서 필드명 그대로) · mock/record/live 클라이언트 · 코드표
backend/app/pipeline   discover → collect → link → extract → judge (+ trend, tracker, scheduler)
backend/app/rules      공공 주체 판별·정규화(orgs: 서울특별시/서울/서울시 → 서울시) · 상태 판정 R1~R5 · 우선순위
backend/app/llm        프롬프트 3종(약속 추출 / 후속 신호 / 보도자료 문장) · 구조화 출력 스키마 · stub/rules/live 어댑터
backend/app/experiment v2 실험: 원천 기사 100건 → 자동 시드 → 군집 → 후속보도 탐지 → 지표(reports/experiment_latest.json)
backend/app/notices    v2 공공기관 게시판 확인(트리거형, 뉴스 도메인 거부) · sites.yaml 등록부
backend/app/press      v2 보도자료 초안 작성기(템플릿 / LLM)
backend/app/api        /api/events, /api/dashboard, /api/press, /api/notices, /api/admin, /api/pipeline, /api/meta
frontend/src/app       /  대시보드 · /events 목록 · /events/[id] 타임라인 · /events/[id]/evidence 근거(+기관 발표 확인) · /press 보도자료 초안 · /about · /admin
docs/                  ARCHITECTURE · API_CONTRACT · CHANGELOG · DEMO_SCRIPT · EXPERIMENT · NOTICES · screenshots/
```

자세한 설계는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), API 응답 형식은 [docs/API_CONTRACT.md](docs/API_CONTRACT.md), 예선/본선 개발 범위 구분은 [docs/CHANGELOG.md](docs/CHANGELOG.md).

## v2 (2026-10-22 멘토 피드백 반영) — 실험·대시보드·보도자료·기관 게시판

멘토 피드백의 요지는 "합성 데이터 말고 실제 API로 돌려서 후속보도가 실제로 얼마나 나오는지 정량 지표로 보여라, 첫 화면은 시민용 대시보드, 후속보도가 없으면 기관 게시판까지 확인해 보도자료 초안까지"였다. 개발 프롬프트 v2 §0 에 결정 표가 있다.

```bash
cd backend
# 실험 (실 API, record 모드로 녹화·재생). --dry-run 은 호출 계획만 출력
BIGKINDS_MODE=record LLM_MODE=rules EMBEDDING_BACKEND=st .venv/bin/python scripts/experiment.py --stage all --dry-run
BIGKINDS_MODE=record LLM_MODE=rules EMBEDDING_BACKEND=st .venv/bin/python scripts/experiment.py --stage all --today 2026-10-22 --max-pages 2
#   --today 는 기준일(녹화 키에 들어가므로 재생할 때도 같은 날짜). .env 의 TODAY_OVERRIDE(가상 픽스처용)는 바꾸지 않는다
.venv/bin/python scripts/experiment.py --stage report --today 2026-10-22   # 라벨 CSV 를 채운 뒤 지표만 다시
#   -> seeds/experiment.draft.yaml (자동 시드) · reports/experiment_<일시>.md · reports/experiment_latest.json
#   -> gold/experiment_labels.csv · cluster_sample.csv · org_sample.csv (사람 열만 비어 있음)
.venv/bin/python scripts/labels_to_gold.py          # 라벨 CSV -> gold/events.yaml
.venv/bin/python scripts/export_dashboard.py        # 일별/재난별/기관별 집계 -> reports/dashboard_<날짜>.json
.venv/bin/python scripts/run_notices.py --all       # 트리거된 사건의 책임기관 게시판 확인 (스케줄러는 05:30 KST)
.venv/bin/python scripts/run_notices.py --check-sites --query 안전   # 등록 게시판 12곳 파싱 점검 (시연 전)
```

- `LLM_MODE=rules`(`app/llm/rules_adapter.py`)는 LLM 키 없이 약속 추출·후속 신호를 규칙으로 낸다. 키가 오면 `LLM_MODE=live` 로만 바꾼다(프로토콜·프롬프트·스키마 동일).
- 화면: `/` 대시보드(KPI·월별/일별 시계열·재난 유형별·기관별 후속보도 현황·최근 후속보도·확인 필요 상위 5) → `/events`(탭 확인 필요 / 후속보도 확인됨, `?org=` 기관 필터) → 타임라인 → 근거(「기관 발표 확인」 패널) → `/press` 보도자료 초안(전체·사건별, 복사·Markdown 다운로드). 캡처는 `docs/screenshots/dashboard_*.png`, `press_*.png`.
- 공공기관 게시판 확인은 뉴스 크롤링이 아니다: `후속보도 부족`·`판단 불가` 상태로 90일 이상 조용한 사건만, 책임기관의 공식 보도자료 게시판(`app/notices/sites.yaml`, 광역 11 + 정책브리핑)만, robots.txt·2초 간격·목록 제목 위주로 읽고, 결과는 상태를 바꾸지 않는 보조 근거다(`docs/NOTICES.md`).
- 실험 방법·호출 수·지표·한계는 `docs/EXPERIMENT.md`, 최신 숫자는 `reports/experiment_latest.json`(대시보드와 보도자료가 같은 파일을 읽는다).

## 예선 목업 평가 결과

`reports/eval_*.md` 참고. 가상 사건 8건(5개 상태 모두 포함)에 대해 Mock 빅카인즈 + Stub LLM + 해시 임베딩으로 측정한 값이며(stub 이라 약속·신호 지표는 배선 검증용), 본선에서는 실제 사건 30건 정답셋으로 다시 측정한다. 2026-10-09 코드 리뷰(139건) 반영 내역은 [docs/CHANGELOG.md](docs/CHANGELOG.md).
