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

가중치      사건별 단어 가중치(rules/weights.py) — link 단계 시작 때 재계산해 events.term_weights 에 저장
              TF = 핵심 문서(시드 문장 + 원천 기사 + 발생일 ±3일 게이트 통과 후보) 중 그 단어가 나온 비율
              IDF = articles 전체 DF 로 log((N+1)/(df+1)) / log(N+1)
              시설명·핵심어·질의 고유어는 하한 0.6(코퍼스에서 흔하면 제외), 지역명·일반 재난어·행정어는 상한 0.05
            weighted_overlap = 기사에 나온 사건 단어 가중치 합 / 상위 10개 합 (0~1)

게이트(필수)  발행일 ≥ 발생일-1일
          AND (지역 OR 시설 OR 책임기관·별칭 일치)                       … scope
          AND (시설명 OR 사건분류 계열 OR 핵심어·연관어 ≥2 OR 약속 행동 키워드 ≥2) … topic
          AND (강한 규칙 OR weighted_overlap ≥ WEIGHT_MIN)                 … weight (지역어+재난어만 겹치는 다른 사건 기사 차단)
          AND 기간 창·지역 조건 (link.scope_checks, LINK_SCOPE_RULES=false 면 끔)   … 2026-10-25, docs/EXPERIMENT.md §7
              ① 발행일 ≤ 발생일 + LINK_EARLY_DAYS(90): 위 게이트 그대로
              ② 그 뒤: 제목에 사건 고유어(가중치 ≥ LINK_UNIQUE_MIN 0.6 인 지역·일반어 아닌 단어 + 시설명)
                       OR (제목에 사건 지역어 AND 하이라이트에 고유어)  OR 약속 행동 키워드 ≥2      (LINK_LATE_MATCH=title_region)
                       — 후속보도는 몇 년 뒤에도 나오므로 하드컷이 아니다 (만덕~센텀 대심도 2026 개통·지반 침하)
                       같은 재난어의 새 사고 속보(구조·사망·실종·특보 …, 복구·원인·재판 같은 돌아보는 말 없음)는 검토 큐 이하 (cap-recur)
                       단 약속 키워드·조치 명사(보강·설치·준설 …)가 제목에 있으면 원래 사건의 재발로 남긴다
              ③ 지역 필수: 기초 지역 사건은 제목·하이라이트·키워드에 사건 지역어(기초명·접미 뗀 꼴·사건 제목/핵심어의 지명·동/읍/면/리)
                 또는 시설명(일반어로만 된 시설명 '산사태 피해지' 는 제외). 광역 사건(도·특별시·광역시)은 사건 고유어 하나 이상
              ④ 제목에 다른 광역·기초 지명(rules/regions.PARENT 로 상위 광역 판정, 보통명사와 겹치는 지명 제외)이 있고
                 사건 지역어·상위 광역·권역(수도권·충청권 …)·시설명이 제목에 없으면 탈락 — 고유어가 있고 새 사고 속보가 아니면 남김
              ⑤ 제목에 사건에 없는 구체 장소명(…역·…항·…공장·…센터 …) + 재난어, 제목에 사건 고유어 없음 → 검토 큐 이하 (cap-place)

판정        강한 규칙 일치 → 자동 연결 (link_method=rule)
              · 시설명이 제목에 있음 + (지역·기관 또는 사건분류)
              · 시설명 + 지역·기관
              · 책임기관 + 약속 행동 키워드 ≥2  (일반어는 rules/terms._STOP 에서 걸러진다)
              · 지역 + 약속 행동 키워드 ≥2 + (시설명 또는 시드 핵심어 1개)
            강한 규칙이라도 weighted_overlap < WEIGHT_RULE_MIN 이면 강한 규칙으로 보지 않는다 (0 = 끔)
            그 외 게이트 통과 → 점수 = (1-WEIGHT_ALPHA)·임베딩 코사인 + WEIGHT_ALPHA·weighted_overlap
              · ≥ LINK_AUTO(0.55)  자동 연결 (embedding)
              · ≥ LINK_REVIEW(0.40) 연결하되 needs_review (검토 큐)
              · 미만              폐기 — 기존 자동 연결이면 삭제 (시드·질의 변화 반영)
            다른 시드의 시설명이 본문에 있고 이 사건 시설명이 없으면(또는 ②·⑤ 의 검토 큐 상한) ≥ LINK_REVIEW 일 때만 검토 큐, 아니면 폐기
재순위       LINK_RERANKER = cross(기본) | features | none   (pipeline/rerank.py · 학습 scripts/train_reranker.py · train_cross_encoder.py · docs/EXPERIMENT.md §9·§10)
            대상 = 위 판정이 link·review 인 후보 — x- 탈락·decide 폐기·관리자 확정·수동 연결은 그대로 (학습 라벨 gold/cluster_full.csv 가 있는 범위와 같다)
            판정 재료는 link.assess() 하나로 만든다 — link 단계와 학습 스크립트가 같은 함수를 쓴다(끄면 결정·link_reason·link_score 가 이전과 같다)
            features  숫자 피처 53개(판정 재료·게이트 사유·cap-·날짜·창 뒤 여부·제목/하이라이트/키워드의 사건 단어·다른 시설(일반어/특정 장소)·
                      제목의 보도 성격·사건 메타) + 제목·하이라이트 토큰 해싱 → 로지스틱 회귀·그래디언트 부스팅 확률 평균 (models/reranker_features.joblib, 4MB)
            cross     ko-sroberta 교차 인코더(사건명·지역·시설·핵심어 ↔ [발생 후 기간] 제목 하이라이트, models/cross_encoder/ gitignore)를
                      RERANK_BLEND(0.5) 비율로 피처 분류기 확률과 섞는다
            p ≥ RERANK_AUTO(0.5) 자동 연결 · ≥ RERANK_REVIEW(0.2) 검토 큐 · 미만 폐기 — 강한 규칙(rule) 연결도 p 로 다시 정한다
            바닥 RERANK_KEEP_LATE_FACILITY: 재순위 전 자동 연결 + 창 뒤 기사 + 제목에 사건 시설명 → 내리지 않는다 (몇 년 뒤 후속 보호, link_reason 에 keep-late-facility)
            RERANK_CROSSFIT: 학습에 쓴 사건은 그 사건을 뺀 fold 모델(교차 인코더는 교차검증 확률)로 — 실험 DB 수치가 학습 재사용으로 부풀지 않게
            link_score = 분류기 확률(사건 간 배타 처리도 이 값으로), link_reason 에 p=·s=(합산 점수), link_method 는 경로 표시 그대로
            모델을 학습한 임베딩과 지금 임베딩이 다르면(가상 픽스처 hash) 쓰지 않는다. 모델·의존성이 없으면 경고 뒤 이전 동작
            기본 cross(2026-10-25 밤 채택): 군집 P/R 0.77/0.78 → 0.90/0.89. 교차 인코더가 없으면 피처 분류기, 학습 임베딩이 다르면 none.
            후속 신호의 사건 단위 지표는 2단계 보강(§4, EXPERIMENT §10)과 함께 0.63/0.81 → 0.62/0.79 (근거까지 맞은 탐지 26 → 31)
            한 기사가 여러 사건에 자동 연결되면 점수(재순위를 켜면 분류기 확률)가 가장 높은 사건만 유지, 나머지는 검토 큐(multi-event)
```

- 임계값은 `.env` 로 조정하며 `scripts/evaluate.py --sweep` 으로 정답셋 대비 표를 만든다(DB 사본에서 실행, 운영 DB 불변).
  실험 라벨(gold/*.csv) 기준 표는 `evaluate.py --labels [--before-db …]`·`--label-sweep "weight_min=… weight_alpha=… link_auto=…"` (app/experiment/label_eval.py — 연결 결정은 link.decide 를 그대로 쓴다).
- `WEIGHT_MIN=0 WEIGHT_ALPHA=0 WEIGHT_RULE_MIN=0` 이면 가중치 도입 전 동작과 같다. link_score 는 합산 점수다.
- 기간 창·지역 조건은 가중치 핵심 문서 선정(발생일 ±3일 게이트 통과 후보)에는 쓰지 않는다 — 조건이 가중치에 기대므로 순환을 피한다(결정성).
- 검토 큐(needs_review) 연결은 집계·후속 신호 어디에도 들어가지 않는다: 소비 경로가 모두 `EventArticle.is_active`(해제·검토 대기 아님)를 거친다
  (judge.followup_articles·trackable_promises, extract, experiment.metrics.collect_facts, API serializers.active_links). 검토 화면만 검토 큐를 읽는다.
- 관리자가 `confirm`/`reject`/`restore` 한 연결은 `manual` 로 고정되어 재평가하지 않는다. 처리 뒤에는 그 사건의 규칙 판정·우선순위·단계를 LLM 없이 다시 계산한다.
- `needs_review` = 검토 대기 연결 OR 미처리 신고 OR 변경이력 표시(`change_flagged`) — `pipeline/review.recompute_needs_review` 한 곳에서 계산한다.
- 임베딩 백엔드: `hash`(문자 n-gram, 의존성 없음, 예선 기본) / `st`(sentence-transformers `jhgan/ko-sroberta-multitask`, 본선). 저장 벡터에 `백엔드:모델명` 을 붙여 모델이 바뀌면 자동 재계산한다.

## 4. LLM 역할 두 가지 (그 밖의 판단은 규칙)

| 역할 | 입력 | 출력(구조화, `messages.parse`) | 프롬프트 |
|---|---|---|---|
| ⓐ 약속 추출 | 인용문 source·quotation + 기사 제목·발행일 + 사건 요약 | `PromiseExtraction` — actor_org·action·deadline_date·deadline_precision·strength(확약/계획/의사표명/해당없음)·confidence | `llm/prompts/promise_v1.md` |
| ⓑ 후속 신호 | 사건 요약 + 추적 약속 목록 + 후속 기사 제목·하이라이트·인용문 | `FollowupSignal` — signal(완료/진행/새로운문제/무관/불명확)·promise_id·matches_promise·evidence_span·confidence | `llm/prompts/followup_v1.md` |

- `LLM_MODE=rules`(rules-v2) 의 ⓑ: 제목·하이라이트·인용문을 문장으로 나누고(제목도 한 문장), 문장 관련도 = (약속 action 과의 임베딩 코사인) + (사건 가중치 단어 점수) 로 상위 `FOLLOWUP_TOP_SENTENCES`(3)개·`FOLLOWUP_SENTENCE_MIN` 이상 문장 안에서만 완료·진행·새로운문제 표현을 본다. 약속 키워드가 없어도 사건 고유어 점수 ≥0.6 인 문장(제목 '…철거 완료')은 후보. confidence 에 관련도를 더한다. 사건 가중치는 `FollowupInput.term_weights` 로 넘기되 LLM 입력 JSON 에서는 빠진다(exclude).
- **2단계 보강(rules-v3, 2026-10-25 밤, EXPERIMENT §10 — 기본값으로 켬)**:
  - 표현 고침(`FOLLOWUP_EXPRESSION_FIXES`): '추가 피해 최소화·예방' 은 새로운문제가 아니고, 앞날을 가리키는 말 옆의 꼬리 없는 '재개장·재개통'('내년 2월 재개장')은 완료가 아니다. 켜면 model 이 `rules-v3` 이라 v2 로 분류한 신호 캐시와 섞이지 않는다.
  - 후속 기사 기준일(`FOLLOWUP_CUTOFF=source`): 원천 발언 다음 날부터 (발생 + 7일 유예 대신 — 라벨은 다음 날의 '철거 완료' 도 후속으로 본다). 신호가 가리킨 약속의 발언일 이전·당일 기사의 신호는 판정 단계(`judge.classify_followups`)가 '불명확' 으로 내린다. `grace` 면 이전 규칙(max(발생 + `FOLLOWUP_GRACE_DAYS`, 가장 이른 추적 약속 발언일)).
  - 근거 기사의 재순위 확률 바닥(`FOLLOWUP_MIN_LINK_PROB=0.6`): link_reason 에 p= 가 있는 자동 연결 중 확률이 낮은 기사는 후속 기사에서 뺀다. 관리자 확정·수동 연결은 그대로, 재순위 바닥 규칙(keep-late-facility)으로 남은 늦은 기사는 완료·새로운문제 근거로만 쓴다('진행' 은 내린다).
  - 비교만 하고 끈 스위치: `FOLLOWUP_ACTION_MATCH`(약속 대상어 겹침만 — 시설·지역 이름 겹침은 같은 종류 행동어가 있을 때, `FollowupInput.identity_terms`), `FOLLOWUP_SKIP_COMMITMENT`(다짐·요구 문장의 진행 표현 제외), `FOLLOWUP_KW_ALONE=false`(고유어 경로 끔). 세부 판정은 `RulesLLM.classify_followup_detail` 이 돌려준다(`scripts/train_followup.py`).
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

후속 기사 = 활성 연결 기사 중 기준일(§4 `FOLLOWUP_CUTOFF`) 뒤 기사이고, 재순위 확률 바닥(`FOLLOWUP_MIN_LINK_PROB`)을 넘는 것 (`judge.followup_articles` — 대시보드·실험 집계도 같은 함수).

"완료할 예정" 은 프롬프트에서 진행으로 분류하므로 R2 가 아니라 R3 로 간다. 상태 문자열은 이 5개뿐이며, 명세 §2-2 의 단정 표현은 코드·프롬프트·UI 어디에도 없다. `tests/test_repo_rules.py` 가 backend·frontend 소스를 검사한다(로컬 데이터 `backend/data`, 실 API 녹화 `fixtures/bigkinds/recordings` 는 실기사 문장이 들어가므로 제외 — 둘 다 gitignore 대상이라 §14 grep 은 깨끗한 체크아웃에서 돌린다).

우선순위(`rules/priority.py`): 기한 경과 +40 · 후속보도 부족 +25 · 판단 불가 +15 · 새로운 문제 +10 · 마지막 후속 기사 경과일/10 (≤20, 후속 기사가 없으면 기준일 이후 경과일) · 보도량 급감(drop_ratio ≥ 0.8, 완결된 직전 3개월 기준) +10 · 확약 존재 +10. API 는 요청 시점 기준으로 다시 계산한다. 판정이 채택한 신호는 `status_judgments.counted_signal_ids` 에 저장되고, 단계(조치진행·결과확인)는 채택 여부와 무관하게 모든 유효 신호로 갱신한다.

## 6. 데이터 이용 범위 (법적 안전장치)

- 뉴스 검색 `content` 는 200자, `hilight` 는 뉴스 200자·인용문 300자 — API 반환 범위만 저장한다(`content_200`, `hilight`).
- 원문은 `provider_link_page` 외부 링크로만 연결한다. **언론사 원문 크롤링·파싱 코드는 없다.** HTML 파서(bs4)는 `app/notices/`(v2 §3 — 공공기관 공식 게시판, 트리거 조건 충족 사건만, 뉴스 도메인은 코드에서 거부)에서만 import 하며 `tests/test_repo_rules.py` 가 그 밖의 import 와 코드 안의 뉴스 도메인 URL 을 금지한다.
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

## 2026-10-12 수정 검증 반영 메모

- **수집(collect)**: 구간마다 `time_line` 로 날짜/월별 건수를 먼저 보고, 상한 안이면 한 번에(`return_size` 한 페이지), 넘치면 날짜(초기 2주)·월(이후) 단위로 상한을 물 채우기 방식으로 배분한다. 초기 구간 상한 `COLLECT_INITIAL_CAP`, 이후 구간은 총상한의 남은 예산을 월별 상한 `COLLECT_MONTH_CAP` 안에서 배분한다. 전재 기사는 상한을 소모하지 않는다. 조용한 사건은 사건당 4회(time_line 2 + search 2)로 끝난다. `total_hits` 는 두 구간의 트렌드 합계다.
- **정렬 인자**: 코드는 지침서의 객체 형식 `{"date": "asc"}` 만 쓴다. 다중 정렬 리스트 `[{"date":"asc"},{"_score":"desc"}]` 는 실서버가 받는지 확인 전이라 `scripts/smoke_live.py` 의 `search_news_sort_list` 단계에서만 시험한다. 구간 상한이 한 페이지 안이라 동률 페이징 문제는 생기지 않는다.
- **접근 키 마스킹**: 빅카인즈는 키가 틀리면 `Invalid Access Key!:<키>` 를 돌려준다. `app/redact.py` 가 등록된 키와 UUID 형태 문자열을 `BigKindsError`·`pipeline_runs.error`·`failures`·로그에서 가리고, `/api/meta` 의 `error_summary` 와 `/api/pipeline/run` 500 detail 은 분류 문구(`빅카인즈 API 오류` 등)만 내보낸다.
- **판정의 잠정 상태**: LLM 호출 상한·실패로 분류를 못 받은 후속 기사는 `Event.pending_followups` 에 건수로 남고, 그 사건은 `needs_review` 로 검토 큐에 들어가며 판정 사유에 "잠정" 이 붙는다. 분류는 최신 기사부터 한다. 규칙 판정은 **현재 약속 집합 해시로 분류된 신호만** 쓴다(약속이 취소·해제되면 옛 신호는 무시).
- **추적 약속 = 근거 기사가 활성 연결인 약속**: 연결이 삭제·해제·검토 대기가 되면 그 기사에서 뽑은 약속은 추적·대표 약속·우선순위·후속 재검색에서 빠진다. `link` 단계만 돌려도 규칙 재판정을 한다.
- **관리자 결정과 평가**: 확정·해제는 `link_method` 를 바꾸지 않고(`+manual` 사유만 추가) `EventArticle.auto_decision`(link|review)이 파이프라인의 마지막 결정으로 남는다. 평가 스크립트는 이 값으로 P/R 을 재서 관리자 해제가 precision 을 부풀리지 않는다. 스윕은 sqlite backup API 로 DB 사본을 만든다(WAL 포함).
- **마이그레이션**: 추가할 수 없는 컬럼이 하나라도 있으면 어떤 DDL 도 실행하지 않고 실패한다(SQLite DDL 은 롤백되지 않는다). JSON `default=list/dict` 는 `DEFAULT '[]'/'{}'` 로 추가한다.
- **공공 주체 판별**: 토큰 단위로 본다. 민간 단어는 토큰 끝 일치('시민안전과장' 은 통과), 기관 직함(사장·대표·이사장)은 앞에 기관 토큰이 있으면 공공, 한 글자 접미(시·군·구·도)는 뒤에 직함·공백이 오거나 별칭·광역명일 때만 기관(사람 이름 '김민구' 제외). 정당은 민간. 시의회는 공공(지자체 별칭에 귀속되지는 않음).

## 지침서와 실제 응답 차이 — 2026-10-12 첫 실호출 (`scripts/smoke_live.py`, 키 활성)

- 7개 엔드포인트 모두 `result=0`. 잘못된 키는 `{"result": -1, "reason": "Invalid Access Key!:<키>"}` 로 키를 되돌려 준다 → `app/redact.py` 마스킹.
- `/search/news` 반환 필드: byline, category(list), category_incident(list), dateline, enveloped_at, hilight, news_id, provider, provider_link_page, provider_news_id, published_at, title, tms_raw_stream. `sort` 는 객체 `{"date":"asc"}` 와 리스트 `[{"date":"asc"},{"_score":"desc"}]` 둘 다 받는다(코드는 객체만 쓴다). `until` 은 제외.
- `provider_link_page` 가 `//www.yeongnam.com/...` 처럼 스킴 없이 온다 → `collect._absolute_link` 가 https 를 붙인다.
- `/search/news` news_ids 조회: content 는 200자.
- `/time_line` 라벨은 day=`20230715`, month=`202307`. 하루 건수가 search 의 같은 날 total_hits 와 일치(날짜 기준·매칭 방식 동일).
- `/search/quotation` 반환 필드: category, category_incident, date(`20261010`), news_id, provider, published_at, quotation, source. **인용문 검색은 기사 본문도 매칭한다**(질의어가 인용문·source 에 없는 문서가 5건 중 4건) — mock 은 인용문만 매칭하므로 실데이터에서는 인용문 수가 더 많다. extract 는 사건에 연결된 news_id 로 거르므로 영향은 호출량뿐.
- `/issue_ranking` 의 `issue_category` 는 코드(`001000000`). `/word_cloud` weight 는 0~20 범위 실수.
- `/changeTracker/news` 는 `dataList`, `message`, `size`(3000), `total_count`(문자열 "4222") — 관대 파서가 처리.

## 2026-10-13 3차 검증 반영 메모

- **수집(이후 구간)**: 고른 월별 표본을 버리고 **조치 상태 질의** `("완료" OR "준공" OR "개통" OR "착공" OR …) AND (기본 질의)` 로 판정 기사를 직접 찾는다. 기본 질의 트렌드가 남은 예산 안이면 한 번에 받고(조용한 사건 4회), 넘치면 상태 질의(월별·큰 달 상·중·하순) + 분기별 표본(`COLLECT_TAIL_SAMPLE`, 트렌드 재사용). 큰 사건에서도 특정 완료 기사는 상태 질의가 직접 찾는다. 예산 이월은 월 상한을 지키고, 순별 배분은 올림으로 상순을 먼저 채운다. 트렌드 실패 시 대체 수집도 셋으로 나눠 받는다. 0건 수집·override 0건은 경고로 남고 `/api/meta.last_run.warnings` 에 나온다.
- **완료 판정 가드**: 미래는 문장 끝 서술어('…할 예정', '…ㄹ 계획', '앞두고 있다', '추진 중이다')로만 본다(보고 동사 꼬리 제거). '모두·전부·100%' 는 완료. 부분 완료는 '일부·절반' 단독이거나 '1단계·우선 … 나머지·2단계' 짝일 때만. 3차 문서의 문장 20개를 테스트로 고정했다.
- **공공 주체**: 행정구역 사전(`rules/regions.py`: 광역·기초 지자체 + 시드 지역·기관·별칭)과 성씨 목록으로 '김민구'(성씨+2자, 사전에 없음)와 '청주시'(사전)를 가른다. 부서 접미(국·실·과·부) 3자는 성씨로 시작하고 부서 명사가 아니면 이름('박정국'). 규칙 기관이 있으면 LLM 값으로 덮어쓰지 않고(병합 키 일관성), 규칙이 없을 때 LLM 값도 사람 이름이면 버려 `""`(기관 미상, 병합 제외)로 둔다. '담당·해당' 은 정당(…당)으로 보지 않는다.
- **잠정·검토 상태**: 추출 대기와 실패 집계는 활성 연결 기사만 센다(연결 해제로 자동 해소). 2회 실패 항목은 관리자 `llm-retry`(초기화·재시도) / `llm-ack`(확인) 로 처리한다. 카드에 `pending_quotations`·`llm_gave_up`·`llm_acked` 가 나온다.
- **재판정 범위**: tracker 단독 실행과 모든 link/relink 는 judge 가 돈 사건만 재판정하고, 배타 처리로 강등된 다른 사건도 포함한다.
- **기동**: 다른 프로세스가 `pipeline.lock` 을 쥐고 있으면 미완료 실행 기록을 닫지 않는다. 성공한 실행은 `error` 를 비운다. `:memory:` DB 도 Lock 은 `backend/data/` 에 둔다.
- **프록시**: `X-Forwarded-Host` 도 같은 사이트로 인정한다. redact 는 키의 하이픈 제거·`%2D`·`_`·대문자 변형도 가린다.


## 2026-10-22 v2 (멘토 피드백 반영) 구조 메모

- **첫 화면**: 시민용 대시보드 `/`(`api/dashboard.py` 집계 6종) → 기자용 목록 `/events`(탭 확인 필요 / 후속보도 확인됨, `org=` 기관 필터) → 타임라인 → 근거(「기관 발표 확인」 패널) → 보도자료 초안 `/press`.
- **실험**(`app/experiment/`, `scripts/experiment.py`): 인용문 검색(월별 × 계열, `_score` 상위 표본, 공공 주체 + 확약·계획 발언) + 이슈 랭킹(재난 어휘 토픽만)으로 원천 기사를 모아 유니크화(지역·시설·±14일·기관) → 자동 시드 → 기존 collect/link/extract/judge → §4.6 지표 → `reports/experiment_latest.json`(대시보드·보도자료가 읽는다). 자세한 방법·호출 수·한계는 `docs/EXPERIMENT.md`.
- **LLM 없이**: `LLM_MODE=rules`(`app/llm/rules_adapter.py`, model `rules-v2`, 2단계 보강을 켜면 `rules-v3`, 10-29 제목형 표현·약속 없는 사건 경로를 켜면 `rules-v4`, 구절 오독 고침·발생 직후 새로운문제 내림(§14.6) 중 하나라도 켜면 `rules-v5` — 지금 기본값)가 약속 추출·후속 신호를 규칙으로 낸다. 프로토콜·프롬프트 파일·스키마는 그대로라 키가 오면 `live` 로만 바꾼다. 보도자료는 `app/press/writer.py` 의 `TemplateWriter`(기본) / `LLMWriter`(live 일 때 문장 다듬기만, 숫자·근거 번호 검증 실패 시 템플릿 유지).
- **공공기관 게시판 확인**(`app/notices/`): 트리거(후속보도 부족·판단 불가 + `NOTICE_TRIGGER_DAYS` 경과) → 등록부 `sites.yaml`(광역 11 + 정책브리핑) → guard(뉴스 도메인 거부·허용 접미) → robots.txt(RFC 9309) → 호스트별 2초 간격 → 목록 제목·날짜·URL, 매칭 항목만 본문 300자 → `org_notices`·`notice_checks`, `Event.org_notice_found`. 상태 5종은 바꾸지 않는 **보조 근거**. 스케줄러 05:30 KST. 등록부와 확인 기록은 `docs/NOTICES.md`.
- **기관 정규화**: `rules/orgs.canonical_org()` — 광역·기초 지자체(청·장·지사 꼬리), 중앙부처 약칭, 공사·공단 — 대시보드 기관별 집계·목록 `org` 필터·보도자료·게시판 등록부가 같은 키를 쓴다.

## 지침서와 실제 응답 차이 — 2026-10-22 실험(원천 기사 수집, 사건 150건 수집)

- `search/quotation` 은 `category_incident` 필터를 걸어도 (월, 계열)당 1만 건 이상(2023-07 산업사고 11,666 · 자연재해 13,265 · 교통 1,605) — 본문까지 매칭하고, 인용문의 분류는 기사 분류를 그대로 물려받아 해외·정치 발언도 섞인다. `return_from` 상한(1000) 때문에 전수 대신 `sort: {"_score": "desc"}` 상위 표본(구간당 200건)을 받는다. 정렬 인자는 받아들여진다.
- `_score` 정렬 인용문 검색은 호출당 5~15초, 30초 타임아웃이 잦다(재시도). 실험은 동시 요청 4개로 나눠 받는다(`bigkinds/live.py` throttle 에 Lock).
- 인용문 문서에는 `title` 이 없다(`fields` 에 넣어도 None) — 원천 기사 제목은 `news_ids` 상세 조회로 받는다.
- 이슈 랭킹 클러스터 100건 `news_ids` 상세 조회는 30초 타임아웃이 잦다 — 앞 20건만 조회해 3계열 비율을 본다.
- 인용문 `category_incident` 는 3단계 명칭('사고>산업사고>화재')으로 온다(코드 아님). 필터는 '사고>산업사고' 명칭과 '2,9' 코드 둘 다 같은 결과.
- **질의 건수 규칙**: `search/news` 의 넓은 질의(재난어 OR + 지역 OR 중앙부처 별칭)는 3년 치 후속 구간에서 collect 가 월·순별 상태 질의로 쪼개 사건당 70~130회를 부른다. 시드 질의는 `time_line` 으로 전 기간 건수를 먼저 보고 **400건 안**(`EXPERIMENT_QUERY_HIT_LIMIT`)으로 좁힌다 — 그러면 사건당 4~5회. 파이프라인의 약속 키워드 재검색도 실데이터에서는 약속 하나에 100회 넘게 번져, 실험은 사건당 한 질의·400건 이하일 때만 받는 실험판(`experiment/followups.py`)을 쓴다.
- 최종 실행 요청 5,406회(녹화 파일 6,678개, 버린 시도 포함), 약 34분. 같은 `--today` 로 다시 돌리면 녹화 재생이라 호출 비용이 없다. 자세한 수치는 `docs/EXPERIMENT.md`.
