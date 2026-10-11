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
                   [SQLite]  events · articles · event_candidates · event_articles · quotations · promises
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
| `record` | `RecordingBigKindsClient` | Live 호출 후 응답을 `fixtures/bigkinds/recordings/<endpoint>/<sha1>.json` 에 원자적으로 저장하고, 있으면 재생한다. `BIGKINDS_REPLAY_ONLY=true` 면 키·네트워크 없이 재생만 하고 녹화에 없는 요청은 `BigKindsError` + `missing` 목록. 요청 키에 `published_at.until`(내일)이 들어가므로 **시연은 녹화한 날짜의 `TODAY_OVERRIDE` 로 고정**한다. |
| `live` | `LiveBigKindsClient` | HTTPS POST `{access_key, argument}`. 타임아웃·전송 오류·429/5xx 만 지수 백오프(429 는 Retry-After), 그 밖의 4xx·비JSON·envelope 오류·`result != 0` 은 즉시 `BigKindsError`. 초당 호출 제한, 호출 요약 로그(첫 문서의 실제 키 포함). 파이프라인은 사건 단위로 잡아 다음 사건으로 넘어간다. |

파이프라인 코드는 `BigKindsClient` 프로토콜만 보며 모드를 모른다. 요청·응답 모델(`app/bigkinds/schemas.py`)의 필드명은 지침서(newstore V1.0)와 동일하다.

### 지침서와 실제 응답 차이 (본선 첫 실호출 후 기록)
- 2026-10-12 `scripts/smoke_live.py`(7개 엔드포인트 최소 호출) 실행. 서버는 응답했으나 전부 `{"request_id": "", "result": -1, "reason": "Invalid Access Key!:<키>"}` — 키 활성화 전. 지침서에 없는 오류 형식이며 `reason` 에 키가 그대로 들어오므로 로그·리포트에서 가린다(`redact`). 키가 유효해지면 같은 스크립트로 `document_keys`(실제 반환 필드), 인용문 검색 범위(`search_quotation_scope`: 인용문 텍스트만인지 본문 포함인지), `size`·`from/interval` 해석을 확인해 여기에 적는다.
- 인용문 검색이 **인용문 텍스트만** 검색한다고 보수적으로 가정한다. 그래서 Mock 도 source+quotation 만 매칭하고, 파이프라인의 인용문 질의는 주제어·기관명 OR 로 넓혀 보낸 뒤 사건에 연결된 news_id 로 거른다.
- 지침서 예시상 `news_id`·`title`·`published_at`·`provider`·`hilight` 는 `fields` 없이도 반환되는 것으로 보이나 확실치 않아, 파이프라인은 필요한 필드를 항상 `fields` 에 명시한다(`DEFAULT_NEWS_FIELDS`). Mock 도 같은 규약을 강제한다.

## 3. 사건–기사 연결 (LLM 사용 금지)

후보 = 수집 단계가 이 사건을 위해 가져온 기사(`event_candidates`, 출처 `seed` 질의 또는 `promise` 질의) ∪ 기존 연결. 로컬 질의 재매칭은 하지 않는다.

```
수집 구간   발생일-1 ~ +14일 (상한 COLLECT_INITIAL_CAP=150) + 이후 월별 구간(상한 COLLECT_MONTH_CAP=80), 총 MAX_ARTICLES_PER_EVENT=300
            잘린 구간은 stats["truncated_windows"] 에 남긴다 (날짜 오름차순 단일 호출은 대형 사건의 첫 주가 상한을 다 채운다)
질의        (시설·핵심어·연관어 OR) AND (지역 OR 기관 OR 시설). 시드 YAML 의 query(사람 지정)가 있으면 그대로, 연관어 확장 없음
약속 재검색  (약속 행동 명사 OR) AND (기관 OR 지역 OR 시설), 약속 발언일 ~ 오늘 — 시설명 대신 다른 표현을 쓰는 후속 기사 대비

게이트(필수)  발행일 ≥ 발생일-1일
          AND (지역 OR 시설 OR 책임기관·별칭 일치)                       … scope
          AND (시설명 OR 사건분류 계열 OR 핵심어·연관어 ≥2 OR 약속 행동 키워드 ≥2) … topic

판정        강한 규칙 일치 → 자동 연결 (link_method=rule)
              · 시설명이 제목에 있음 + (지역·기관 또는 사건분류)
              · 시설명 + 지역·기관
              · 책임기관 + 약속 행동 키워드 ≥2  (일반어는 rules/terms._STOP 에서 걸러진다)
              · 지역 + 약속 행동 키워드 ≥2 + (시설명 또는 시드 핵심어 1개)
            그 외 게이트 통과 → 임베딩 코사인 점수
              · ≥ LINK_AUTO(0.55)  자동 연결 (embedding)
              · ≥ LINK_REVIEW(0.40) 연결하되 needs_review (검토 큐)
              · 미만              폐기 — 기존 자동 연결이면 삭제 (시드·질의 변화 반영)
            다른 시드의 시설명이 본문에 있고 이 사건 시설명이 없으면 ≥ LINK_REVIEW 일 때만 검토 큐, 아니면 폐기
            한 기사가 여러 사건에 자동 연결되면 점수가 가장 높은 사건만 유지, 나머지는 검토 큐(multi-event)
```

- 임계값은 `.env` 로 조정하며 `scripts/evaluate.py --sweep` 으로 정답셋 대비 표를 만든다(DB 사본에서 실행, 운영 DB 불변).
- 관리자가 `confirm`/`reject`/`restore` 한 연결은 `manual` 로 고정되어 재평가하지 않는다. 처리 뒤에는 그 사건의 규칙 판정·우선순위·단계를 LLM 없이 다시 계산한다.
- `needs_review` = 검토 대기 연결 OR 미처리 신고 OR 변경이력 표시(`change_flagged`) — `pipeline/review.recompute_needs_review` 한 곳에서 계산한다.
- 임베딩 백엔드: `hash`(문자 n-gram, 의존성 없음, 예선 기본) / `st`(sentence-transformers `jhgan/ko-sroberta-multitask`, 본선). 저장 벡터에 `백엔드:모델명` 을 붙여 모델이 바뀌면 자동 재계산한다.

## 4. LLM 역할 두 가지 (그 밖의 판단은 규칙)

| 역할 | 입력 | 출력(구조화, `messages.parse`) | 프롬프트 |
|---|---|---|---|
| ⓐ 약속 추출 | 인용문 source·quotation + 기사 제목·발행일 + 사건 요약 | `PromiseExtraction` — actor_org·action·deadline_date·deadline_precision·strength(확약/계획/의사표명/해당없음)·confidence | `llm/prompts/promise_v1.md` |
| ⓑ 후속 신호 | 사건 요약 + 추적 약속 목록 + 후속 기사 제목·하이라이트·인용문 | `FollowupSignal` — signal(완료/진행/새로운문제/무관/불명확)·promise_id·matches_promise·evidence_span·confidence | `llm/prompts/followup_v1.md` |

- `evidence_span` 은 코드가 검증한다: 완료·진행·새로운문제는 발췌(≤100자)가 입력의 부분 문자열이어야 하고(없으면 `불명확`), 발췌가 '예정·목표' 표현이면 완료→진행으로 낮춘다.
- 실패(한도 초과·서버 오류·파싱 실패·stub 미스)는 `LLMFailure` 로 올리고 **저장하지 않는다** — 다음 실행에서 재시도. 캐시는 (news_id, prompt_version) 에 더해 model 과 분류 당시 추적 약속 집합 해시가 같을 때만 재사용한다. LLM 호출은 DB 쓰기 트랜잭션 밖에서 하고, 실행당 호출 상한(`LLM_MAX_CALLS_PER_RUN`)을 둔다.
- 공공 주체 판별(`rules/actors.py`)은 규칙이 먼저 걸러 민간인·피의자 발언은 LLM 에 보내지 않는다. 주체는 기관 단위로 정규화한다.
- `LLM_MODE=stub` 은 `fixtures/llm/stub_answers.json` 정답을 재생한다(키 불필요). `live` 는 Anthropic API(`LLM_MODEL`, 기본 `claude-opus-5`), 시스템 프롬프트에 `cache_control` 을 걸어 프롬프트 캐시를 쓴다.
- 모든 LLM 산출물에 `model`, `prompt_version` 을 남겨 재현·비교가 가능하다.

## 5. 상태 판정 규칙 (`rules/status.py`, 단위 테스트)

유효 신호 = `matches_promise` AND `confidence ≥ SIGNAL_MIN_CONF(0.6)` AND signal ∈ {완료, 진행, 새로운문제}

| 순서 | 규칙 | 상태 |
|---|---|---|
| R1 | 가장 최근 유효 신호가 새로운문제(≥ 0.7). 같은 날 동률이면 새로운문제 우선 | 새로운 문제 발생 |
| R2 | 완료 ≥ 0.7 1건 이상, 또는 완료 ≥ 0.6 으로 서로 다른 발췌·날짜 2건 이상(전재 중복은 1건) | 조치 완료 근거 확인 |
| R3 | 진행 1건 이상 | 조치 진행 정황 확인 |
| R4 | 후속 기사 0건 / 마지막 후속 기사 60일 이전 / 기한 경과 + 완료·진행 없음 | 후속보도 부족 |
| R5 | 그 외 (+ 사유 필수) | 판단 불가 |

"완료할 예정" 은 프롬프트에서 진행으로 분류하므로 R2 가 아니라 R3 로 간다. 상태 문자열은 이 5개뿐이며, 명세 §2-2 의 단정 표현은 코드·프롬프트·UI 어디에도 없다. `tests/test_repo_rules.py` 가 backend·frontend 소스를 검사한다(로컬 데이터 `backend/data`, 실 API 녹화 `fixtures/bigkinds/recordings` 는 실기사 문장이 들어가므로 제외 — 둘 다 gitignore 대상이라 §14 grep 은 깨끗한 체크아웃에서 돌린다).

우선순위(`rules/priority.py`): 기한 경과 +40 · 후속보도 부족 +25 · 판단 불가 +15 · 새로운 문제 +10 · 마지막 후속 기사 경과일/10 (≤20, 후속 기사가 없으면 기준일 이후 경과일) · 보도량 급감(drop_ratio ≥ 0.8, 완결된 직전 3개월 기준) +10 · 확약 존재 +10. API 는 요청 시점 기준으로 다시 계산한다. 판정이 채택한 신호는 `status_judgments.counted_signal_ids` 에 저장되고, 단계(조치진행·결과확인)는 채택 여부와 무관하게 모든 유효 신호로 갱신한다.

## 6. 데이터 이용 범위 (법적 안전장치)

- 뉴스 검색 `content` 는 200자, `hilight` 는 뉴스 200자·인용문 300자 — API 반환 범위만 저장한다(`content_200`, `hilight`).
- 원문은 `provider_link_page` 외부 링크로만 연결한다. 크롤링·원문 파싱 코드는 없다 (`grep -r "beautifulsoup\|selenium\|requests.get(" backend/` 결과 없음).
- 근거로 쓰인 기사가 변경이력 API 에서 `Update`/`Cancelled` 로 나오면 사건에 `change_flagged` 를 세운다(관리자가 `clear-change-flag` 로 내린다). Cancelled 근거 기사의 약속은 추적을 멈추고, 바뀐 기사의 후속 신호는 지운 뒤 즉시 재분류·재판정한다.

## 7. 저장소 구조

```
migyeol/
├── backend/app/{bigkinds,db,pipeline,llm,rules,api}   FastAPI + 파이프라인
├── backend/fixtures/{bigkinds,llm,seeds,gold}          가상 코퍼스·stub 정답·가상 시드·정답셋 (make_fixtures.py 생성, mock·테스트 전용)
├── backend/seeds/events.yaml                           실사건 시드 (record/live, 사람 확정)
├── backend/gold/events.yaml                            실사건 정답 데이터셋 (record/live, 본선 30건)
├── backend/scripts/{make_fixtures,run_pipeline,evaluate,smoke_live}.py
├── backend/tests/                                      규칙·클라이언트·E2E·API
├── frontend/                                           Next.js 3화면 + 관리자
└── docs/{ARCHITECTURE,API_CONTRACT,CHANGELOG,DEMO_SCRIPT}.md
```
