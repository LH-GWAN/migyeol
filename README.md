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
.venv/bin/python scripts/make_fixtures.py --today 2026-09-11     # 가상 사건 8건 픽스처
TODAY_OVERRIDE=2026-09-11 .venv/bin/python scripts/run_pipeline.py --stage all --reset
.venv/bin/uvicorn app.main:app --reload --port 8000

# 프런트 (다른 터미널)
cd frontend
npm install && cp .env.local.example .env.local
npm run dev -- -p 3000        # http://localhost:3000
```

저장소 루트의 `.env.example` 을 `.env` 로 복사해 모드를 정한다. 예선 기본값은 `BIGKINDS_MODE=mock`, `LLM_MODE=stub`, `EMBEDDING_BACKEND=hash`, `TODAY_OVERRIDE=2026-09-11`(픽스처 기준일 고정).

```bash
cd backend && .venv/bin/python -m pytest -q                       # 테스트
TODAY_OVERRIDE=2026-09-11 .venv/bin/python scripts/evaluate.py     # 정답셋 대비 평가 -> reports/eval_<날짜>.md
TODAY_OVERRIDE=2026-09-11 .venv/bin/python scripts/evaluate.py --sweep link_auto=0.45,0.55,0.65
```

## 본선 전환 (API 키 발급 후)

1. `.env` 에 `BIGKINDS_MODE=record`, `BIGKINDS_ACCESS_KEY=...` — 실 API 응답이 `backend/fixtures/bigkinds/recordings/` 에 저장·재생된다 (PT 오프라인 시연 안전장치).
2. 첫 실호출 로그의 `keys` 로 실제 반환 필드를 확인하고 `docs/ARCHITECTURE.md` "지침서와 실제 응답 차이"에 기록한다.
3. `run_pipeline.py --stage discover` 로 `seeds/events.draft.yaml` 초안을 만들고 사람이 20~30건을 `seeds/events.yaml` 로 확정한다.
4. `LLM_MODE=live`, `ANTHROPIC_API_KEY=...` (기본 모델 `claude-opus-5`, 비용이 부담되면 `LLM_MODEL=claude-sonnet-5`).
5. `pip install -e ".[embeddings]"` 후 `EMBEDDING_BACKEND=st` (한국어 sentence-transformers).
6. `gold/events.yaml` 을 실제 사건 30건으로 교체하고 `evaluate.py --sweep` 으로 임계값을 정한다.

## 빅카인즈 API 활용 (지침서 newstore V1.0)

| API | 경로 | 사용 위치 | 활용 방식 |
|---|---|---|---|
| 오늘의 이슈 | `/issue_ranking` | `pipeline/discover.py` | 날짜별 상위 이슈의 `news_cluster` 를 뉴스 상세 조회로 받아 `category_incident` 가 재난·안전 3계열이면 사건 후보 |
| 뉴스 검색 | `/search/news` | `pipeline/collect.py` | 시설·사건명 × 지역·기관 질의, `published_at` 발생일 이후, `sort date asc`, 100건 페이징, `fields` 명시 |
| 뉴스 상세 조회 | `/search/news` (`news_ids`) | `pipeline/discover.py` | 이슈 클러스터 기사의 분류 확인 |
| 연관어 분석 | `/word_cloud` | `pipeline/collect.py` | 사건 핵심어 연관어 3개로 질의 보강 (후속 기사의 다른 표현 대비) |
| 뉴스 인용문 검색 | `/search/quotation` | `pipeline/extract.py` | `source`(발언 주체)·`quotation`(발언 내용) — 약속 구조화의 핵심 데이터원 |
| 키워드 트렌드 | `/time_line` | `pipeline/trend.py` | 월별 기사 건수 → 보도량 급감(`drop_ratio`) 정량화 |
| 뉴스 변경이력 | `/changeTracker/news` | `pipeline/tracker.py` | 근거 기사가 `Update`/`Cancelled` 면 사건 재검토 표시 |

데이터 이용 범위: 뉴스 `content` 200자·`hilight` 200/300자 등 API 반환 범위만 저장하며 원문은 `provider_link_page` 외부 링크로만 연결한다. 크롤링 코드는 없다.

## 구조

```
backend/app/bigkinds   요청·응답 모델(지침서 필드명 그대로) · mock/record/live 클라이언트 · 코드표
backend/app/pipeline   discover → collect → link → extract → judge (+ trend, tracker, scheduler)
backend/app/rules      공공 주체 판별·정규화 · 상태 판정 R1~R5 · 우선순위
backend/app/llm        프롬프트 2종(약속 추출 / 후속 신호) · 구조화 출력 스키마 · stub/live 어댑터
backend/app/api        /api/events, /api/admin, /api/pipeline, /api/meta
frontend/src/app       /  목록 · /events/[id] 타임라인 · /events/[id]/evidence 근거 · /admin
docs/                  ARCHITECTURE · API_CONTRACT · CHANGELOG · DEMO_SCRIPT · screenshots/
```

자세한 설계는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), API 응답 형식은 [docs/API_CONTRACT.md](docs/API_CONTRACT.md), 예선/본선 개발 범위 구분은 [docs/CHANGELOG.md](docs/CHANGELOG.md).

## 예선 목업 평가 결과

`reports/eval_*.md` 참고. 가상 사건 8건(5개 상태 모두 포함)에 대해 Mock 빅카인즈 + Stub LLM + 해시 임베딩으로 측정한 값이며, 본선에서는 실제 사건 30건 정답셋으로 다시 측정한다.
