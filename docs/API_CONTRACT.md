# 미결 백엔드 API 계약 (v0, Phase 0)

- Base URL: `http://localhost:8000` (환경변수 `NEXT_PUBLIC_API_BASE`)
- 모든 응답은 `application/json; charset=utf-8`. 날짜는 `YYYY-MM-DD`, 일시는 ISO 8601(KST, `+09:00`).
- 사건 상태 `status`는 아래 5개 문자열 중 하나이며 다른 값은 존재하지 않는다.
  - `조치 완료 근거 확인` / `조치 진행 정황 확인` / `새로운 문제 발생` / `후속보도 부족` / `판단 불가`
- 기사 응답에는 전문이 없다. `hilight`는 200자 이내, 원문은 `provider_link_page` 외부 링크로만 연결한다.
- 모든 상태 표시 옆에 고정 면책 문구를 노출한다. 서버는 `disclaimer` 필드로 같은 문장을 내려준다.
  - `보도가 없다는 사실은 조치가 없었다는 근거가 아니라, 확인이 필요하다는 신호입니다.`

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
  "link_reason": "region+facility; category_incident=재해>자연재해",
  "change_status": "ok"
}
```
- `phase` ∈ `발생` | `원인조사` | `대응발표` | `조치진행` | `결과확인` | `기타`
- `link_method` ∈ `seed` | `rule` | `embedding` | `manual`
- `change_status` ∈ `ok` | `updated` | `cancelled` (변경이력 API 결과)

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
  "is_trackable": true,
  "confidence": 0.9,
  "deadline_passed": false,
  "quotation": {
    "news_id": "01500701.20250718100000001",
    "source": "가온시 안전건설국장",
    "quotation": "내년 10월까지 배수펌프 용량을 2배로 늘리겠다",
    "provider": "부산일보",
    "published_at": "2025-07-18"
  },
  "evidence_article": { "...": "ArticleOut" }
}
```
- `strength` ∈ `확약` | `계획` | `의사표명` | `해당없음`
- `deadline_precision` ∈ `day` | `month` | `year` | `none`

### TrendOut
```json
{
  "interval": "month",
  "series": [{ "label": "202507", "hits": 41 }, { "label": "202508", "hits": 9 }],
  "peak_label": "202507",
  "peak_hits": 41,
  "recent_mean": 1.3,
  "drop_ratio": 0.97,
  "markers": { "occurred_at": "2025-07-15", "deadline_date": "2026-10-31", "today": "2026-09-11" }
}
```

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
  "priority_score": 78.5,
  "needs_check": true,
  "needs_review": false,
  "deadline_date": "2026-10-31",
  "deadline_passed_days": null,
  "last_followup_at": "2026-08-10",
  "days_since_last_followup": 32,
  "followup_count": 3,
  "evidence_count": 6,
  "article_count": 14,
  "primary_promise": {
    "actor_org": "가온시",
    "action": "달빛지하차도 배수펌프 용량을 2배로 증설한다",
    "deadline_date": "2026-10-31",
    "deadline_precision": "month",
    "strength": "확약"
  },
  "trend_sparkline": [41, 9, 3, 0, 1, 0, 2, 0, 0, 1, 0, 0, 1, 0],
  "disclaimer": "보도가 없다는 사실은 조치가 없었다는 근거가 아니라, 확인이 필요하다는 신호입니다."
}
```
- `needs_check` = `status ∈ {후속보도 부족, 판단 불가, 새로운 문제 발생}` 또는 기한 경과
- `deadline_passed_days` : 기한이 지났으면 경과 일수(정수), 아니면 `null`
- `primary_promise` : 추적 가능한 약속 중 `확약` 우선, 없으면 `계획`, 없으면 `null`

## 엔드포인트

### GET `/api/events`
쿼리: `status`(5개 중 하나), `region`, `incident_type`(접두 일치, 예 `재해>자연재해`), `needs_check`(`true`/`false`), `sort`(`priority`|`recent`|`occurred`, 기본 `priority`), `page`(기본 1), `size`(기본 20, 최대 100)
```json
{
  "items": [ { "...": "EventCard" } ],
  "total": 7,
  "page": 1,
  "size": 20,
  "filters": {
    "statuses": ["조치 완료 근거 확인", "조치 진행 정황 확인", "새로운 문제 발생", "후속보도 부족", "판단 불가"],
    "regions": ["가온시", "누리군"],
    "incident_types": ["재해>자연재해>홍수", "사고>산업사고>붕괴"]
  }
}
```

### GET `/api/events/{id}`
```json
{
  "card": { "...": "EventCard" },
  "summary": "2025년 7월 15일 집중호우로 …(시드 topic_content)",
  "seed_query": "(\"달빛지하차도\" OR \"지하차도 침수\") AND (\"가온시\" OR \"가온시청\")",
  "status_reason": "R4_stale_followup",
  "status_reason_text": "마지막 후속 기사가 60일 이전이고 완료·진행 신호가 없음",
  "latest_judgment": {
    "status": "후속보도 부족",
    "reason": "R4_stale_followup",
    "confidence": 0.5,
    "unresolved_reason": null,
    "judged_at": "2026-09-11T04:00:12+09:00"
  },
  "promises": [ { "...": "PromiseOut" } ],
  "timeline": [
    { "phase": "발생", "articles": [ { "...": "ArticleOut" } ] },
    { "phase": "원인조사", "articles": [] },
    { "phase": "대응발표", "articles": [ { "...": "ArticleOut" } ] },
    { "phase": "조치진행", "articles": [] },
    { "phase": "결과확인", "articles": [] },
    { "phase": "기타", "articles": [ { "...": "ArticleOut" } ] }
  ],
  "trend": { "...": "TrendOut" },
  "disclaimer": "보도가 없다는 사실은 …"
}
```
- `timeline`은 항상 6개 phase를 이 순서로 모두 포함한다(빈 배열 허용). 각 phase 내부는 발행일 오름차순.

### GET `/api/events/{id}/evidence`
```json
{
  "event_id": 1,
  "title": "가온시 달빛지하차도 침수 사고",
  "status": "조치 완료 근거 확인",
  "confidence": 0.82,
  "rule": {
    "name": "R2_completion_evidence",
    "text": "완료 신호 1건(신뢰도 0.82)이 기준 0.7 이상",
    "thresholds": { "signal_min_conf": 0.6, "completion_min_conf": 0.7, "stale_followup_days": 60 }
  },
  "unresolved_reason": null,
  "signals": [
    {
      "id": 11,
      "signal": "완료",
      "promise_id": 3,
      "matches_promise": true,
      "confidence": 0.82,
      "evidence_span": "배수펌프 증설 공사를 마치고 이날 가동을 시작했다",
      "rationale": "완료형 표현과 약속 대상(배수펌프 증설)이 일치",
      "counted": true,
      "article": { "...": "ArticleOut" }
    }
  ],
  "low_confidence_links": [
    { "article": { "...": "ArticleOut" }, "link_score": 0.47, "link_reason": "region only" }
  ],
  "last_followup_at": "2026-08-10",
  "next_scheduled_run": "2026-09-12T04:00:00+09:00",
  "judged_at": "2026-09-11T04:00:12+09:00",
  "disclaimer": "보도가 없다는 사실은 …"
}
```
- `signal` ∈ `완료` | `진행` | `새로운문제` | `무관` | `불명확`
- `counted` : 상태 판정에 실제 반영된 유효 신호인지(`matches_promise` 이고 `confidence ≥ signal_min_conf`)

### GET `/api/events/{id}/trend`
`TrendOut` 그대로.

### POST `/api/events/{id}/reports`
요청
```json
{ "type": "wrong_link", "news_id": "01500701.20250716093000001", "comment": "다른 지하차도 기사입니다" }
```
- `type` ∈ `wrong_link` | `wrong_status` | `wrong_promise` | `other`, `news_id`는 선택
응답 `201`
```json
{ "id": 5, "event_id": 1, "type": "wrong_link", "created_at": "2026-09-11T15:02:11+09:00" }
```

### GET `/api/admin/review-queue`
```json
{
  "events": [ { "...": "EventCard (needs_review=true)" } ],
  "links": [
    { "event_id": 1, "event_title": "가온시 달빛지하차도 침수 사고", "article": { "...": "ArticleOut" }, "link_score": 0.47, "link_reason": "region only" }
  ],
  "reports": [
    { "id": 5, "event_id": 1, "event_title": "…", "type": "wrong_link", "news_id": "…", "comment": "…", "created_at": "…", "resolved": false }
  ]
}
```

### POST `/api/admin/links/{event_id}/{news_id}`
요청 `{ "action": "confirm" }` 또는 `{ "action": "reject" }`
응답 `{ "event_id": 1, "news_id": "…", "link_method": "manual", "confirmed": true }` (reject 시 연결 행 삭제, `confirmed:false`)

### POST `/api/pipeline/run` (개발·시연용, `PIPELINE_API_ENABLED=true`일 때만)
요청 `{ "stage": "all", "event_id": null }` — `stage` ∈ `discover`|`collect`|`link`|`extract`|`judge`|`all`
응답 `{ "run_id": 12, "stage": "all", "ok": true, "stats": { "events": 7, "articles": 96, "links": 88, "promises": 9, "signals": 31 }, "duration_ms": 1840 }`

### GET `/api/meta`
```json
{
  "service": "미결",
  "bigkinds_mode": "mock",
  "llm_mode": "stub",
  "llm_model": "claude-opus-5",
  "embedding_backend": "hash",
  "event_count": 7,
  "today": "2026-09-11",
  "last_pipeline_run": { "stage": "all", "finished_at": "2026-09-11T14:59:31+09:00", "ok": true, "stats": { "...": "" } },
  "disclaimer": "보도가 없다는 사실은 …"
}
```

### GET `/api/health`
`{ "ok": true }`

## 오류 형식
`{ "detail": "event 99 not found" }` (FastAPI 기본). 404, 422, 403(`PIPELINE_API_ENABLED=false`).
