# 미결(未結) 아키텍처

> 뉴스 기사를 독립 문서가 아니라 하나의 사건에 속한 **상태 변화 기록**으로 다룬다.
> `발생 → 원인 조사 → 대응 발표 → 조치 진행 → 결과 확인`

## 1. 전체 흐름

```
 [빅카인즈 OpenAPI]                        BIGKINDS_MODE = mock | record | live
  issue_ranking ─┐
  search/news ───┤   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
  word_cloud ────┼─▶ │ 1 discover   │─▶ │ 2 collect    │─▶ │ 3 link       │─▶ │ 4 extract    │
  time_line ─────┤   │ 사건 후보     │   │ 기사 수집·정제 │   │ 규칙 게이트   │   │ 인용문→약속   │
  search/quotation┤  └──────────────┘   └──────────────┘   │ + 임베딩      │   │ (LLM 역할 ⓐ) │
  changeTracker ─┘                                          └──────────────┘   └──────┬───────┘
                                                                     ▲                 │ 약속 행동 키워드로
                                                                     └── 재연결 ◀──────┘ 후속 기사 추가 검색
                                                  ┌──────────────┐   ┌──────────────┐
                                                  │ 6 tracker    │   │ 5 judge      │
                                                  │ 변경이력 확인  │   │ 후속 신호(LLM ⓑ)│
                                                  │ → 재검토 표시  │   │ + 규칙 R1~R5  │
                                                  └──────────────┘   │ + 우선순위     │
                                                                     └──────┬───────┘
                                        ┌──────────────────────────────────┘
                                        ▼
                   [SQLite]  events · articles · event_articles · quotations · promises
                             followup_signals · status_judgments · trend_snapshots · reports · pipeline_runs
                                        │
                                        ▼
                   [FastAPI /api]  ──▶  [Next.js]  ① 후속 확인 필요 목록  ② 사건 타임라인  ③ 근거·불확실성  (+ 관리자)
```

- 파이프라인은 `scripts/run_pipeline.py --stage discover|collect|link|extract|judge|tracker|all` 로 단계별 실행이 가능하고, 모든 실행은 `pipeline_runs` 에 기록된다.
- APScheduler(`SCHEDULER_ENABLED=true`)가 매일 04:00 KST 에 `all`, 05:00 KST 에 `tracker` 를 실행한다.

## 2. 빅카인즈 클라이언트 3모드

| 모드 | 클래스 | 동작 |
|---|---|---|
| `mock` | `MockBigKindsClient` | `fixtures/bigkinds/*.json` 코퍼스를 **실제 API 와 같은 검색 의미론**(AND/OR/NOT/구문/괄호, published_at, category_incident, fields, 페이징)으로 서비스한다. 예선 기본값. |
| `record` | `RecordingBigKindsClient` | Live 호출 후 응답을 `fixtures/bigkinds/recordings/<endpoint>/<sha1>.json` 에 저장하고, 있으면 재생한다. 본선 개발·PT 오프라인 시연용. |
| `live` | `LiveBigKindsClient` | HTTPS POST `{access_key, argument}`, 429/5xx 지수 백오프 3회, 초당 호출 제한, 호출 요약 로그. |

파이프라인 코드는 `BigKindsClient` 프로토콜만 보며 모드를 모른다. 요청·응답 모델(`app/bigkinds/schemas.py`)의 필드명은 지침서(newstore V1.0)와 동일하다.

### 지침서와 실제 응답 차이 (본선 첫 실호출 후 기록)
- (아직 없음) — Live 첫 호출 시 `call_log` 의 `keys` 로 실제 반환 키를 확인하고 여기에 적는다.
- 지침서 예시상 `news_id`·`title`·`published_at`·`provider`·`hilight` 는 `fields` 없이도 반환되는 것으로 보이나 확실치 않아, 파이프라인은 필요한 필드를 항상 `fields` 에 명시한다(`DEFAULT_NEWS_FIELDS`). Mock 도 같은 규약을 강제한다.

## 3. 사건–기사 연결 (LLM 사용 금지)

```
게이트(필수)  발행일 ≥ 발생일-1일
          AND (지역 OR 시설 OR 책임기관·별칭 일치)                       … scope
          AND (시설명 일치 OR 사건분류 계열 일치 OR 시드·연관어 키워드 ≥2 OR 약속 행동 키워드 ≥2) … topic

판정        강한 규칙 일치 → 자동 연결 (link_method=rule)
              · 시설명이 제목에 있음
              · 시설명 + 지역/기관
              · 지역/기관 + 약속 행동 키워드 ≥2
            그 외 게이트 통과 → 임베딩 코사인 점수
              · ≥ LINK_AUTO(0.55)  자동 연결 (embedding)
              · ≥ LINK_REVIEW(0.40) 연결하되 needs_review (검토 큐)
              · 미만              폐기
            같은 지역 다른 시드의 시설명이 본문에 있고 이 사건 시설명이 없으면 무조건 검토 큐
```

- 임계값은 `.env` 로 조정하며 `scripts/evaluate.py --sweep` 으로 정답셋 대비 표를 만든다.
- 관리자가 `confirm`/`reject` 한 연결은 `manual` 로 고정되어 재평가하지 않는다.
- 임베딩 백엔드: `hash`(문자 n-gram, 의존성 없음, 예선 기본) / `st`(sentence-transformers `jhgan/ko-sroberta-multitask`, 본선). `EMBEDDING_BACKEND` 로 교체.

## 4. LLM 역할 두 가지 (그 밖의 판단은 규칙)

| 역할 | 입력 | 출력(구조화, `messages.parse`) | 프롬프트 |
|---|---|---|---|
| ⓐ 약속 추출 | 인용문 source·quotation + 기사 제목·발행일 + 사건 요약 | `PromiseExtraction` — actor_org·action·deadline_date·deadline_precision·strength(확약/계획/의사표명/해당없음)·confidence | `llm/prompts/promise_v1.md` |
| ⓑ 후속 신호 | 사건 요약 + 추적 약속 목록 + 후속 기사 제목·하이라이트·인용문 | `FollowupSignal` — signal(완료/진행/새로운문제/무관/불명확)·promise_id·matches_promise·evidence_span·confidence | `llm/prompts/followup_v1.md` |

- `evidence_span` 은 코드가 입력의 부분 문자열인지 검증하고 아니면 `불명확` 으로 강등한다.
- 공공 주체 판별(`rules/actors.py`)은 규칙이 먼저 걸러 민간인·피의자 발언은 LLM 에 보내지 않는다. 주체는 기관 단위로 정규화한다.
- `LLM_MODE=stub` 은 `fixtures/llm/stub_answers.json` 정답을 재생한다(키 불필요). `live` 는 Anthropic API(`LLM_MODEL`, 기본 `claude-opus-5`), 시스템 프롬프트에 `cache_control` 을 걸어 프롬프트 캐시를 쓴다.
- 모든 LLM 산출물에 `model`, `prompt_version` 을 남겨 재현·비교가 가능하다.

## 5. 상태 판정 규칙 (`rules/status.py`, 단위 테스트)

유효 신호 = `matches_promise` AND `confidence ≥ SIGNAL_MIN_CONF(0.6)` AND signal ∈ {완료, 진행, 새로운문제}

| 순서 | 규칙 | 상태 |
|---|---|---|
| R1 | 가장 최근 유효 신호가 새로운문제(≥ 0.7) | 새로운 문제 발생 |
| R2 | 완료 ≥ 0.7 1건 이상, 또는 완료 ≥ 0.6 2건 이상 | 조치 완료 근거 확인 |
| R3 | 진행 1건 이상 | 조치 진행 정황 확인 |
| R4 | 후속 기사 0건 / 마지막 후속 기사 60일 이전 / 기한 경과 + 완료·진행 없음 | 후속보도 부족 |
| R5 | 그 외 (+ 사유 필수) | 판단 불가 |

"완료할 예정" 은 프롬프트에서 진행으로 분류하므로 R2 가 아니라 R3 로 간다. 상태 문자열은 이 5개뿐이며 `미이행` 같은 단정 표현은 어디에도 없다.

우선순위(`rules/priority.py`): 기한 경과 +40 · 후속보도 부족 +25 · 판단 불가 +15 · 새로운 문제 +10 · 마지막 후속 기사 경과일/10 (≤20) · 보도량 급감(drop_ratio ≥ 0.8) +10 · 확약 존재 +10.

## 6. 데이터 이용 범위 (법적 안전장치)

- 뉴스 검색 `content` 는 200자, `hilight` 는 뉴스 200자·인용문 300자 — API 반환 범위만 저장한다(`content_200`, `hilight`).
- 원문은 `provider_link_page` 외부 링크로만 연결한다. 크롤링·원문 파싱 코드는 없다 (`grep -r "beautifulsoup\|selenium\|requests.get(" backend/` 결과 없음).
- 근거로 쓰인 기사가 변경이력 API 에서 `Update`/`Cancelled` 로 나오면 사건을 `needs_review` 로 표시한다.

## 7. 저장소 구조

```
migyeol/
├── backend/app/{bigkinds,db,pipeline,llm,rules,api}   FastAPI + 파이프라인
├── backend/fixtures/{bigkinds,llm}                     예선용 가상 코퍼스 · stub 정답 (make_fixtures.py 생성)
├── backend/seeds/events.yaml                           추적 대상 사건 시드 (사람 확정)
├── backend/gold/events.yaml                            정답 데이터셋 (본선: 실제 30건으로 교체)
├── backend/scripts/{make_fixtures,run_pipeline,evaluate}.py
├── backend/tests/                                      규칙·클라이언트·E2E·API
├── frontend/                                           Next.js 3화면 + 관리자
└── docs/{ARCHITECTURE,API_CONTRACT,CHANGELOG,DEMO_SCRIPT}.md
```
