# 미결 백엔드 API 계약 (v0.2, 2026-10-12 리뷰 반영)

- Base URL: `http://localhost:8000`. 프런트 서버 컴포넌트는 `API_BASE_INTERNAL` 로 직접 호출하고, 브라우저는 같은 출처의 `/api/...` 를 쓴다(Next `rewrites` 가 백엔드로 프록시).
- 모든 응답은 `application/json; charset=utf-8`. 날짜는 `YYYY-MM-DD`, 일시는 ISO 8601 KST(`+09:00`). 서버 TZ 와 무관하게 KST 로 기록한다.
- 사건 상태 `status`는 아래 5개 문자열 중 하나이며 다른 값은 존재하지 않는다.
  - `조치 완료 근거 확인` / `조치 진행 정황 확인` / `새로운 문제 발생` / `후속보도 부족` / `판단 불가`
- 기사 응답에는 전문이 없다. `hilight`는 200자 이내, 원문은 `provider_link_page` 외부 링크로만 연결한다.
- 모든 상태 표시 옆에 고정 면책 문구를 노출한다. 서버는 `disclaimer` 필드로 같은 문장을 내려준다.
  - `보도가 없다는 사실은 조치가 없었다는 근거가 아니라, 확인이 필요하다는 신호입니다.`
- 목록·상세·검토 큐·meta 는 **활성 사건**(현재 시드에 있는 사건, `active=true`)만 다룬다. 시드에서 빠진 사건은 404.
- `ADMIN_TOKEN` 이 설정돼 있으면 `/api/admin/*` 와 `/api/pipeline/run` 에 `X-Admin-Token` 헤더가 필요하다(없으면 401, 비교는 constant-time). 비어 있으면 검사하지 않는다. 프런트는 서버 측(서버 컴포넌트, `/api/admin|pipeline` Route Handler 프록시)에서만 헤더를 붙인다.
- **잠정 판정**: `EventCard.pending_followups`(분류 못 받은 후속 기사)·`pending_quotations`(추출 못 받은 공공 인용문)가 0 보다 크면 잠정 상태다(호출 상한·실패 이월, 활성 연결 기사만 센다). 그 사건은 `needs_review=true` 이고 판정 사유 끝에 "잠정 판정" 이 붙는다. `llm_gave_up` 은 같은 항목이 2회 실패해 불명확/해당없음으로 기록된 상태, `llm_acked` 는 관리자가 확인한 상태.
- 약속의 `actor_org` 가 비면 `"기관 미상"` 으로 내려간다 (규칙·LLM 모두 기관을 못 찾았거나 사람 이름 형태). 미상 약속은 서로 병합되지 않는다.

## 공통 객체

### ArticleOut
```json
{
  "news_id": "01500701.20250716093000001",
  "title": "가온시 달빛지하차도 침수… 차량 12대 고립",
  "provider": "부산일보",
  "published_at": "2025-07-16",
  "provider_link_page": "https://example.invalid/news/01500701.20250716093000001",
  "hilight": "15일 밤 집중호우로 가온시 달빛지하차도가 침수돼 차량 12대가 고립됐다…",
  "byline": "김가온",
  "phase": "발생",
  "link_method": "rule",
  "link_score": 0.81,
  "link_reason": "region+facility+facility-title+incident+seed-kw=2",
  "change_status": "ok"
}
```
- `phase` ∈ `발생` | `원인조사` | `대응발표` | `조치진행` | `결과확인` | `기타`
- `link_method` ∈ `rule` | `embedding` | `manual` (관리자 확정·해제·복구)
- `link_reason` 은 통과한 게이트 조건을 `+` 로 이은 문자열. `other-facility`, `multi-event` 가 붙으면 검토 큐 사유다.
- `change_status` ∈ `ok` | `updated` | `cancelled` (변경이력 API 결과)
- null 가능: `published_at`, `provider_link_page`, `byline`. 사건에 연결되지 않은 기사(예: 약속 근거 기사가 연결 목록에 없을 때)는 `phase`·`link_method`·`link_score`·`link_reason`도 null

### PromiseOut
```json
{
  "id": 3,
  "actor_org": "가온시",
  "actor_raw": "가온시 안전건설국장",
  "action": "달빛지하차도 배수펌프 용량을 2배로 증설한다",
  "deadline_raw": "내년 10월까지",
  "deadline_date": "2026-10-31",
  "deadline_precision": "month",
  "strength": "확약",
  "is_trackable": true,   // 추적 중인지 — 근거 기사가 이 사건의 활성 연결(해제·검토 대기 아님)일 때만 true
  "merged_into_id": null,
  "confidence": 0.9,
  "deadline_passed": false,
  "quotation": { "news_id": "…", "source": "가온시 안전건설국장", "quotation": "내년 10월까지 …", "provider": "부산일보", "published_at": "2025-07-18" },
  "evidence_article": { "...": "ArticleOut" },
  "merged_news_ids": []
}
```
- `strength` ∈ `확약` | `계획` | `의사표명` | `해당없음`, `deadline_precision` ∈ `day` | `month` | `year` | `none`
- null 가능: `deadline_raw`, `deadline_date`, `quotation`, `quotation.published_at`, `evidence_article`
- 중복으로 병합된 약속은 `merged_into_id` 에 대표 약속 id 가 있고, 상세의 `promises` 목록에서는 빠진다(대표의 `merged_news_ids` 에 근거 기사가 남는다).

### TrendOut
```json
{
  "interval": "month",
  "series": [{ "label": "202507", "hits": 41 }, { "label": "202508", "hits": 9 }],
  "peak_label": "202507",
  "peak_hits": 41,
  "recent_mean": 1.3,
  "drop_ratio": 0.97,
  "markers": { "occurred_at": "2025-07-15", "deadline_date": "2026-10-31", "deadline_precision": "month", "today": "2026-09-11" }
}
```
- `recent_mean` 은 완결된 직전 3개월 평균(진행 중인 이번 달 제외). `markers.deadline_*` 는 카드의 대표 약속 기준, 없으면 null.

### EventCard
```json
{
  "id": 1,
  "title": "가온시 달빛지하차도 침수 사고",
  "incident_type": "재해>자연재해>홍수",
  "region": "가온시",
  "facility": "달빛지하차도",
  "responsible_org": "가온시",
  "occurred_at": "2025-07-15",
  "status": "후속보도 부족",
  "status_confidence": 0.5,
  "judged": true,
  "priority_score": 64.0,
  "needs_check": true,
  "pending_followups": 0,
  "needs_review": true,
  "deadline_date": "2026-10-31",
  "deadline_precision": "month",
  "deadline_passed_days": null,
  "last_followup_at": "2026-03-05",
  "days_since_last_followup": 190,
  "followup_count": 2,
  "evidence_count": 2,
  "article_count": 7,
  "primary_promise": { "actor_org": "가온시", "action": "…", "deadline_date": "2026-10-31", "deadline_precision": "month", "strength": "확약" },
  "trend_sparkline": [5, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0],
  "disclaimer": "보도가 없다는 사실은 …"
}
```
- **대표 약속(`primary_promise`)** = 기한이 지난 추적 약속(가장 오래 지난 것) > 확약 > 계획. `deadline_date`·`deadline_precision`·`deadline_passed_days` 는 모두 이 약속 기준이라 서로 모순되지 않는다. (우선순위 점수의 '기한 경과 +40' 도 같은 약속.)
- `needs_check` = `status ∈ {후속보도 부족, 판단 불가, 새로운 문제 발생}` 또는 (기한 경과 AND status ≠ 조치 완료 근거 확인). 완료 사건은 기한이 지나도 확인 대상이 아니다.
- `needs_review` = 검토 대기 연결 OR 미처리 신고 OR 변경이력(tracker) 표시. 각각 독립적으로 켜지고 꺼진다.
- `priority_score` 는 **요청 시점** 기준으로 다시 계산한 값(기한 경과·마지막 후속 기사 경과일은 오늘 기준, 추이는 최신 스냅샷).
- `evidence_count` = 추적 약속의 근거 기사(활성 연결) + 최신 판정이 채택한 신호의 기사. 무관·불명확·해제·검토 대기·삭제 기사는 세지 않는다.
- `judged=false` 인 사건(수집·연결만 끝남): `status_confidence` null, `priority_score` 0, `needs_check` false.

## 엔드포인트

### GET `/api/events`
쿼리: `status`(5개 중 하나), `region`, `incident_type`(접두 일치), `needs_check`(`true`/`false`), `org`(v2: 정규화 기관 키 — `rules/orgs.canonical_org(responsible_org)`, 빈 기관은 `기관 미상`), `followup`(v2: `true` = 후속보도 확인됨(완료·진행), `false` = 그 밖), `sort`(`priority`|`recent`|`occurred`, 기본 `priority`), `page`(기본 1), `size`(기본 20, 최대 100)
- `recent` 는 마지막 후속 기사일 내림차순이며 후속 기사가 없는 사건은 뒤로 간다.
```json
{ "items": [ { "...": "EventCard" } ], "total": 7, "page": 1, "size": 20,
  "filters": { "statuses": [...5개...], "regions": ["가온시", "누리군"], "incident_types": ["재해>자연재해>홍수", "사고>산업사고>붕괴"], "orgs": ["가온시", "누리군"] } }
```

### GET `/api/events/{id}`
```json
{
  "card": { "...": "EventCard" },
  "summary": "2025년 7월 15일 집중호우로 …",
  "seed_query": "(\"달빛지하차도\" OR \"지하차도 침수\" OR \"배수펌프\") AND (\"가온시\" OR \"가온시청\" OR \"가온시장\" OR \"달빛지하차도\")",
  "status_reason": "R4_stale_followup",
  "status_reason_text": "마지막 후속 기사가 190일 전(기준 60일)이고 완료·진행 신호가 없음",
  "latest_judgment": { "status": "후속보도 부족", "reason": "R4_stale_followup", "confidence": 0.5, "unresolved_reason": "…", "judged_at": "2026-09-11T04:00:12+09:00" },
  "promises": [ { "...": "PromiseOut" } ],
  "timeline": [ { "phase": "발생", "articles": [ { "...": "ArticleOut" } ] }, { "phase": "원인조사", "articles": [] }, "… 6개 phase 고정 순서" ],
  "trend": { "...": "TrendOut" },
  "disclaimer": "…"
}
```
- `timeline` 은 활성 연결(해제·검토 대기 제외)이고 삭제되지 않은 기사만, 6개 phase 고정 순서, 발행일 오름차순.
- 판정 이력이 없으면 `latest_judgment` null, `status_reason` null, `status_reason_text` = `아직 상태 판정이 실행되지 않음`.
- id 는 1 이상 2^31-1 이하. 범위 밖(0, 음수, 2^31 초과)은 `422`(검증 오류), 비활성·없는 사건은 `404 {"detail": "event N not found"}`. 프런트 `parseId` 가 범위 밖 id 를 미리 404 페이지로 보낸다.

### GET `/api/events/{id}/evidence`
```json
{
  "event_id": 1, "title": "…", "status": "조치 완료 근거 확인", "judged": true, "confidence": 0.9,
  "rule": { "name": "R2_completion_evidence", "text": "완료 신호 1건(최고 신뢰도 0.90)이 기준 0.7 이상", "thresholds": { "signal_min_conf": 0.6, "completion_min_conf": 0.7, "stale_followup_days": 60 } },
  "unresolved_reason": null,
  "signals": [
    { "id": 11, "signal": "완료", "promise_id": 3, "matches_promise": true, "confidence": 0.9,
      "evidence_span": "전 구간 라이닝 보강을 완료했으며", "rationale": "…", "counted": true, "considered": false, "active": true, "article": { "...": "ArticleOut" } }
  ],
  "low_confidence_links": [ { "article": { "...": "ArticleOut" }, "link_score": 0.47, "link_reason": "facility+facility-title+seed-kw=2" } ],
  "last_followup_at": "2025-12-22",
  "next_scheduled_run": null,
  "judged_at": "2026-09-11T04:00:12+09:00",
  "disclaimer": "…"
}
```
- `signals` 는 현재 prompt_version 의 신호만. 해제된 연결의 신호는 빠진다. `active=false` 는 검토 대기 연결이거나 삭제된 기사.
- `counted` = 최신 판정 규칙(R1~R5)이 근거로 채택한 신호. 일치하고 기준 이상이어도 우선순위가 높은 규칙이 먼저 적용되면 false 다. `considered` = R5 가 참고로 보여 주는 기준 미달 신호.
- `next_scheduled_run` 은 `SCHEDULER_ENABLED=false` 면 null.
- 판정 전 사건: `judged=false`, `confidence` null, `rule.text`·`unresolved_reason` = `아직 상태 판정이 실행되지 않음`.

### GET `/api/events/{id}/trend` — `TrendOut` 그대로.

### POST `/api/events/{id}/reports`
요청 `{ "type": "wrong_link", "news_id": "01500701.20250716093000001", "comment": "다른 지하차도 기사입니다" }`
- `type` ∈ `wrong_link` | `wrong_status` | `wrong_promise` | `other`. `news_id` 는 선택이며 `\d{8}\.\d+` 형식이고 **이 사건에 연결된 기사**여야 한다(아니면 422). `comment` 는 2000자 이내. 한 사건의 미처리 신고가 50건을 넘으면 429.
응답 `201 { "id": 5, "event_id": 1, "type": "wrong_link", "created_at": "…" }`. 신고가 생기면 사건의 `needs_review` 가 켜진다.

### GET `/api/admin/review-queue`
```json
{
  "events": [ { "...": "EventCard (needs_review=true)" } ],
  "links": [ { "event_id": 1, "event_title": "…", "article": { "...": "ArticleOut" }, "link_score": 0.47, "link_reason": "…" } ],
  "rejected_links": [ { "...": "같은 형식 — 관리자가 해제한 연결 (되돌리기 대상)" } ],
  "reports": [ { "id": 5, "event_id": 1, "event_title": "…", "type": "wrong_link", "news_id": "…", "comment": "…", "created_at": "…", "resolved": false } ]
}
```
- `reports` 는 미처리(`resolved=false`) 신고만.

### POST `/api/admin/links/{event_id}/{news_id}`
요청 `{ "action": "confirm" | "reject" | "restore" }`
응답 `{ "event_id": 1, "news_id": "…", "link_method": "rule", "confirmed": true, "rejected": false }`
- `link_method` 는 파이프라인의 연결 방식(rule/embedding)이 그대로 남는다. 사람의 결정은 `confirmed`/`rejected` 플래그와 `link_reason` 끝의 `+manual` 로 표시하며, 다음 실행에서 다시 평가하지 않는다.
- `reject` 는 연결 행을 지우지 않고 `rejected=true` 로 표시한다. 해제된 연결은 타임라인·근거·검토 큐에서 빠지고 재실행해도 다시 자동 연결되지 않는다. `restore` 는 해제를 되돌려 검토 큐로 보낸다.
- 어떤 액션이든 뒤에 **그 사건의 규칙 판정·우선순위·단계를 다시 계산**한다(LLM·외부 API 호출 없음). 그래서 유일한 완료 근거를 해제하면 상태가 즉시 바뀐다.

### POST `/api/admin/reports/{report_id}/resolve`
요청 본문 없음. 응답 `{ "id": 5, "resolved": true }` — 없는 id 면 404. 처리되면 그 신고 몫의 `needs_review` 가 꺼진다.

### POST `/api/admin/events/{event_id}/clear-change-flag`
변경이력(tracker) 검토를 마쳤을 때 표시를 내린다. 응답 `{ "event_id": 1, "needs_review": false }`.

### POST `/api/admin/events/{event_id}/llm-retry` · `/llm-ack`
- `llm-retry`: LLM 실패 카운터를 초기화하고 2회 실패로 기록된 `해당없음` 약속·`불명확` 신호를 지운다 → 다음 실행에서 다시 시도. 응답 `{ "event_id": 1, "removed": 2, "needs_review": true }`.
- `llm-ack`: 2회 실패 항목을 사람이 확인했다고 표시 → 검토 큐에서 빠진다(새 실패가 나면 표시가 풀린다). 응답 `{ "event_id": 1, "needs_review": false }`.

### POST `/api/pipeline/run` (개발·시연용, `PIPELINE_API_ENABLED=true` 일 때만)
요청 `{ "stage": "all", "event_id": null }` — `stage` ∈ `discover`|`collect`|`link`|`extract`|`judge`|`tracker`|`all`
응답 `{ "run_id": 12, "stage": "all", "ok": true, "stats": { "events": 8, "articles": 46, "links": 42, "review": 1, "promises": 8, "signals": 18, "followups": 18 }, "duration_ms": 1840 }`
- `stats` 는 항상 숫자 요약. 실행 중이면 `409`, 없는 `event_id` 는 `404`(범위 밖은 `422`), 꺼져 있으면 `403`, 실패는 `500 {"detail": "<분류 문구>"}`(`/api/meta` 의 `error_summary` 와 같은 분류. 원문·트레이스백은 `pipeline_runs.error` 와 서버 로그에만).
- `event_id` 를 주면 tracker 단계는 생략된다.

### GET `/api/meta`
```json
{
  "service": "미결",
  "bigkinds_mode": "mock",
  "llm_mode": "stub",
  "llm_model": "stub",
  "embedding_backend": "hash",
  "event_count": 8,
  "today": "2026-09-11",
  "today_overridden": true,
  "scheduler_enabled": false,
  "warnings": [],
  "last_pipeline_run": { "stage": "all", "event_id": null, "finished_at": "…", "ok": true, "error_summary": null, "stats": { "...": "" } },
  "last_run": { "stage": "link", "event_id": 3, "finished_at": "…", "ok": false, "error_summary": "빅카인즈 API 오류", "stats": {} },
  "disclaimer": "…"
}
```
- `bigkinds_mode` ∈ `mock` | `record` | `live`. `llm_model` 은 stub 모드면 `"stub"`.
- `last_pipeline_run` = 마지막으로 **성공한 전체 갱신**(stage `all`/`judge`, 전체 사건). `last_run` = 성공 여부와 무관한 마지막 실행. 실행 중이면 `finished_at` 이 null 이고 `ok` 는 false 다. 서버가 중단돼 끝나지 못한 실행은 다음 기동 때 실패(`서버 종료로 중단`)로 닫힌다.
- `today_overridden` 이 true 면 기준일이 `TODAY_OVERRIDE` 로 고정된 상태. `warnings` 는 설정 경고 문장 목록.
- `error_summary` 는 분류 문구만이다: `빅카인즈 API 오류` / `LLM 호출 오류` / `실행 중복` / `입력·설정 오류` / `질의 구문 오류` / `시드 오류` / `서버 종료로 중단` / `실행 오류`. `last_run.warnings` 는 수집 경고 문장(최대 20개), `failures` 는 사건 단위로 격리된 실패 수. 서버 원문(키·경로가 섞일 수 있음)은 `pipeline_runs.error` 와 서버 로그에만 남는다. `/api/pipeline/run` 의 500 `detail` 도 같은 분류 문구다.

### GET `/api/health` — `{ "ok": true }`

## v2 (2026-10-22 멘토 피드백 반영) 추가 엔드포인트

집계 공통 규칙: 사건 = `Event.active=true`; 후속 기사 = 활성 연결만(`judge.followup_articles`, 카드 `followup_count` 와 같음); 유효 신호 = 현재 prompt_version · 현재 추적 약속 집합 해시 · `rules.status.is_valid`; **후속보도 확인 사건 = 유효 신호 ≥ 1**; 기관 키 = `canonical_org(responsible_org)`(빈 값은 `기관 미상`). 목록 화면 숫자와 어긋나지 않도록 같은 함수를 쓴다.

### GET `/api/dashboard/summary`
`{ events_total, with_followup, followup_rate, overdue, with_org_notice, followup_articles, orgs, by_status{5개}, last_run, as_of, bigkinds_mode, experiment, disclaimer }`
- `overdue` = 대표 약속 기한이 지났고 상태 ≠ `조치 완료 근거 확인`. `with_org_notice` = `Event.org_notice_found` 합.
- `experiment` = `reports/experiment_latest.json`(`EXPERIMENT_LATEST_PATH`)에서 큰 목록(`by_incident_type`·`by_org`·`timeseries`·`daily`)을 뺀 요약, 파일이 없으면 `null`.

### GET `/api/dashboard/timeseries?interval=month|day&from=&until=`
`[{ bucket, occurred, followup_articles, confirmed_followups, dead, injured }]` — 빈 구간은 0. 기본 범위 = 가장 이른 발생일 ~ 기준일(일별은 최대 1500일, 초과·`from > until` 은 422). `dead/injured` 는 실험 JSON 의 월별 값이 있으면 그것, 없으면 `Event.dead/injured` 합계, 둘 다 없으면 `null`(추정하지 않는다).

### GET `/api/dashboard/by-incident`
`[{ incident_type, label, events, with_followup, rate, subtypes }]` — 계열(앞 두 단계, 예 `재해>자연재해`) 단위.

### GET `/api/dashboard/by-org?sort=followup|rate|events`
`[{ org, events, with_followup, rate, latest_followup_at, last_article_at, confirmed_signals, event_ids, raw_names }]` — 기본 `followup` 내림차순(동률은 `rate`). `raw_names` 는 정규화 전 이름들.

### GET `/api/dashboard/recent-followups?limit=10` (1~50)
`[{ event_id, event_title, event_status, org, signal, confidence, evidence_span, news_id, title, provider, published_at, link }]` 최신순.

### GET `/api/dashboard/notice-cases?per_kind=1` (1~5)
`{ items[], kinds{}, disclaimer }` — 기관 공식 게시판에서 발표가 확인된 **사례**(2026-10-27 멘토 피드백). 종류별 `per_kind` 건.
- `kind` = `notice_without_followup`(발생일 이후의 기관 발표가 있는데 그 발표일 이후로 확인된 후속보도(유효 신호)가 없는 사건. 사건에 시설명이 있으면 발표의 매칭 용어·요약이 시설명과 겹쳐야 한다. 매칭 점수 → 책임기관 자체 게시판 → 최근 발표 순) | `notice_before_followup`(발생일 이후의 기관 발표로부터 30일 이상 지나서야 다음 후속보도(유효 신호)가 확인된 사건, 간격 긴 순).
- item = `{ kind, kind_label, event_id, title, status, org, incident_type, occurred_at, last_article_at, articles_after, latest_confirmed_at, notices_count, notice{ title, published_at, url, site_key, site_name, site_url, snippet, match_score, matched_terms }, gap_days(①: 발생→발표, ②: 발표→후속보도), followup_after_at(②: 발표 뒤 처음 확인된 후속보도일) }`. 보조 근거이며 상태 5종을 바꾸지 않는다.

### GET `/api/dashboard/experiment`
`experiment_latest.json` 그대로(개발 프롬프트 v2 §4.6 스키마). 없으면 404, 깨졌으면 503.

### GET `/api/press/draft` · `/api/press/draft?event_id=` · `/api/press/draft?format=brief|article` · `/api/press/events`
보도자료 초안(공개). `{ scope: "all"|"event", event_id, format: "brief"|"article", title, subtitle, header_note, markdown, sections: [{heading, body}], sources: [{ref, news_id, title, provider, published_at, link}], generated_at, writer, stats, data_source: "experiment"|"db", experiment_generated_at, disclaimer }`
- `format=brief`(기본) 는 **사건 브리프**: 절 제목 + 목록(전체 `요약 / 재난 유형별 현황 / 후속보도가 확인된 기관과 사례 / 후속보도가 확인되지 않은 사건 / 기관 공식 게시판 확인 결과 / 분석 방법 / 유의 사항 / 문의 / 근거 기사`, 사건별 `사건 요약 / 발표된 약속 / 후속보도 / 기관 발표 확인 / 현재 상태 / 문의 / 근거 기사`). `subtitle` 은 null.
- `format=article` 은 **기사체**: 사실 기반 제목 + `subtitle`(부제 1줄) + 절 `리드 / 본문 / 배경 / 마무리 / 근거 기사`. 글머리표·표 없이 문단(빈 줄 구분)만 쓴다. 본문은 발표 당시 기관 발언 인용 → 뒤 보도에서 확인된 것 → 확인되지 않은 것 → 기관 공식 게시판 발표(날짜·제목) 순. `markdown` 은 절 제목 없이 문단을 잇고 「근거 기사」 만 `##` 로 둔다. 그 밖의 값(`format` 외)은 `essay` 등 → 422.
- 두 형식 모두 숫자·날짜·근거 번호는 같은 집계(`press/data.py`)에서만 오고 **같은 사건에서 `sources` 는 번호까지 동일**하다. 본문의 `[n]` 은 `sources[].ref` 와 대응한다. `writer` 는 `template-v1`(브리프) / `article-v1`(기사체), `LLM_MODE=live` 일 때는 두 형식 모두 `llm-press-v1(<모델>)` 이 같은 검사(숫자·근거 번호 불변, 금지 표현 없음)로 리드·본문 문장만 다듬는다. `data_source` 가 `experiment` 면 리드·유형별·기관별 숫자를 실험 리포트에서 가져온다.
- `/api/press/events` 는 사건별 초안 선택 목록 `[{id, title, status, org, occurred_at, has_followup, notice_found}]`.

### GET `/api/notices/events/{event_id}` (공개, 외부 요청 없음)
`{ event_id, org, triggered, reason, basis_date, trigger_days, state, state_label, note, checked_at, sites: [{name, org, url}], items: [{title, published_at, url, snippet, match_score, matched_terms, site_name}], disclaimer }`
- `state` ∈ `found` / `not_triggered` / `no_site` / `not_found` / `not_checked` / `error`. 기관 게시판 항목은 **보조 근거**이며 5개 상태를 바꾸지 않는다(v2 §3).

### POST `/api/notices/run` (관리자 토큰)
`{ event_id? }` → 트리거된 사건에 대해 확인 실행. `{ events, triggered, found, items, requests }`. CLI `scripts/run_notices.py --all|--event-id N|--check-sites` 와 같은 함수. 스케줄러는 매일 05:30 KST 에 같은 일을 한다.

## 오류 형식
`{ "detail": "..." }` (FastAPI 기본). 401(관리자 토큰), 403(파이프라인 API 꺼짐), 404, 409(실행 중), 422, 429, 500(요약 메시지만).
