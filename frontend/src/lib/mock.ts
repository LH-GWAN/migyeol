/**
 * Sample data that matches docs/API_CONTRACT.md exactly.
 * Used when NEXT_PUBLIC_USE_MOCK=1 so the UI can be verified without the backend.
 * All names/places are fictional (가온시, 누리군 ...).
 */
import type {
  AdminLinkAction,
  AdminLinkOut,
  ResolveReportOut,
  ArticleOut,
  EventCard,
  EventDetailOut,
  EventListOut,
  EventListParams,
  EvidenceOut,
  HealthOut,
  MetaOut,
  PipelineRunIn,
  PipelineRunOut,
  PromiseOut,
  ReportIn,
  ReportOut,
  ReviewQueueOut,
  TimelinePhase,
  TrendOut,
} from "./types";
import { PHASES, STATUSES } from "./types";
import { DISCLAIMER } from "./constants";

const TODAY = "2026-09-11";
const JUDGED_AT = "2026-09-11T04:00:12+09:00";
const NEXT_RUN = "2026-09-12T04:00:00+09:00";

function article(
  partial: Partial<ArticleOut> & Pick<ArticleOut, "news_id" | "title" | "provider" | "published_at" | "hilight" | "phase">,
): ArticleOut {
  return {
    provider_link_page: `https://example.invalid/news/${partial.news_id}`,
    byline: null,
    link_method: "rule",
    link_score: 0.8,
    link_reason: "region+facility",
    change_status: "ok",
    ...partial,
  };
}

// ---------------------------------------------------------------------------
// Event 1: 가온시 달빛지하차도 침수 사고 — 후속보도 부족 (contract example)
// ---------------------------------------------------------------------------

const E1_ARTICLES: ArticleOut[] = [
  article({
    news_id: "01500701.20250716093000001",
    title: "가온시 달빛지하차도 침수… 차량 12대 고립",
    provider: "부산일보",
    published_at: "2025-07-16",
    hilight: "15일 밤 집중호우로 가온시 달빛지하차도가 침수돼 차량 12대가 고립됐다…",
    byline: "김가온",
    phase: "발생",
    link_method: "seed",
    link_score: 1,
    link_reason: "seed",
  }),
  article({
    news_id: "01100101.20250716113000002",
    title: "달빛지하차도 침수 현장 구조 마무리… 인명 피해 없어",
    provider: "연합뉴스",
    published_at: "2025-07-16",
    hilight: "가온시 달빛지하차도 침수 현장에서 고립됐던 운전자 14명이 모두 구조됐다. 인명 피해는 없는 것으로 확인됐다.",
    phase: "발생",
    link_score: 0.88,
    link_reason: "region+facility; category_incident=재해>자연재해",
  }),
  article({
    news_id: "01500601.20250717090000003",
    title: "달빛지하차도 침수 원인은 배수펌프 용량 부족",
    provider: "국제신문",
    published_at: "2025-07-17",
    hilight: "가온시가 17일 침수 원인을 조사한 결과 배수펌프 용량이 설계 기준에 미치지 못한 것으로 확인됐다.",
    byline: "박누리",
    phase: "원인조사",
    link_score: 0.84,
    link_reason: "region+facility",
  }),
  article({
    news_id: "01500701.20250718100000001",
    title: "가온시 “내년 10월까지 달빛지하차도 배수펌프 2배 증설”",
    provider: "부산일보",
    published_at: "2025-07-18",
    hilight: "가온시 안전건설국장은 18일 브리핑에서 “내년 10월까지 배수펌프 용량을 2배로 늘리겠다”고 밝혔다.",
    byline: "김가온",
    phase: "대응발표",
    link_score: 0.91,
    link_reason: "region+facility; actor=가온시",
  }),
  article({
    news_id: "01100201.20250719180000004",
    title: "가온시, 지하차도 침수 대책 발표… 자동차단시설 설치",
    provider: "KBS",
    published_at: "2025-07-19",
    hilight: "가온시는 19일 관내 지하차도 4곳에 연내 진입 자동차단시설을 설치하겠다고 밝혔다.",
    phase: "대응발표",
    link_score: 0.79,
    link_reason: "region+facility; actor=가온시",
  }),
  article({
    news_id: "01500701.20251104090000005",
    title: "달빛지하차도 배수펌프 증설 설계 용역 착수",
    provider: "부산일보",
    published_at: "2025-11-04",
    hilight: "가온시는 달빛지하차도 배수펌프 증설을 위한 설계 용역에 착수했다고 4일 밝혔다.",
    phase: "조치진행",
    link_score: 0.86,
    link_reason: "region+facility",
  }),
  article({
    news_id: "01500601.20250802090000006",
    title: "장마철 지하차도 안전점검 전국 확대",
    provider: "국제신문",
    published_at: "2025-08-02",
    hilight: "가온시 달빛지하차도 침수 사고 이후 행정안전부가 전국 지하차도 안전점검을 확대한다.",
    phase: "기타",
    link_method: "embedding",
    link_score: 0.63,
    link_reason: "embedding sim 0.63; region",
  }),
  article({
    news_id: "01500701.20260810090000007",
    title: "가온시, 여름철 재난 대비 훈련 실시",
    provider: "부산일보",
    published_at: "2026-08-10",
    hilight: "가온시는 10일 지하차도 침수 상황을 가정한 여름철 재난 대비 훈련을 실시했다.",
    phase: "기타",
    link_method: "embedding",
    link_score: 0.61,
    link_reason: "embedding sim 0.61; region",
  }),
];

const E1_LOW_LINK_ARTICLE = article({
  news_id: "01500601.20260722090000008",
  title: "가온시 은하지하차도 배수 공사 지연에 주민 불편",
  provider: "국제신문",
  published_at: "2026-07-22",
  hilight: "가온시 은하지하차도 배수 공사가 예정보다 두 달 늦어지면서 주민 불편이 이어지고 있다.",
  phase: "기타",
  link_method: "embedding",
  link_score: 0.47,
  link_reason: "region only",
});

const E1_PROMISES: PromiseOut[] = [
  {
    id: 3,
    actor_org: "가온시",
    actor_raw: "가온시 안전건설국장",
    action: "달빛지하차도 배수펌프 용량을 2배로 증설한다",
    deadline_raw: "내년 10월까지",
    deadline_date: "2026-10-31",
    deadline_precision: "month",
    strength: "확약",
    is_trackable: true,
    confidence: 0.9,
    deadline_passed: false,
    quotation: {
      news_id: "01500701.20250718100000001",
      source: "가온시 안전건설국장",
      quotation: "내년 10월까지 배수펌프 용량을 2배로 늘리겠다",
      provider: "부산일보",
      published_at: "2025-07-18",
    },
    evidence_article: E1_ARTICLES[3],
  },
  {
    id: 4,
    actor_org: "가온시",
    actor_raw: "가온시",
    action: "관내 지하차도 4곳에 진입 자동차단시설을 설치한다",
    deadline_raw: "연내",
    deadline_date: "2025-12-31",
    deadline_precision: "year",
    strength: "계획",
    is_trackable: true,
    confidence: 0.75,
    deadline_passed: true,
    quotation: {
      news_id: "01100201.20250719180000004",
      source: "가온시",
      quotation: "관내 지하차도 4곳에 연내 진입 자동차단시설을 설치하겠다",
      provider: "KBS",
      published_at: "2025-07-19",
    },
    evidence_article: E1_ARTICLES[4],
  },
];

const E1_SPARK = [41, 9, 3, 0, 1, 0, 2, 0, 0, 1, 0, 0, 1, 0, 1];

const E1_CARD: EventCard = {
  id: 1,
  title: "가온시 달빛지하차도 침수 사고",
  incident_type: "재해>자연재해>홍수",
  region: "가온시",
  facility: "달빛지하차도",
  responsible_org: "가온시",
  occurred_at: "2025-07-15",
  status: "후속보도 부족",
  status_confidence: 0.5,
  priority_score: 78.5,
  needs_check: true,
  needs_review: false,
  deadline_date: "2026-10-31",
  deadline_passed_days: null,
  last_followup_at: "2026-08-10",
  days_since_last_followup: 32,
  followup_count: 3,
  evidence_count: 6,
  article_count: 14,
  primary_promise: {
    actor_org: "가온시",
    action: "달빛지하차도 배수펌프 용량을 2배로 증설한다",
    deadline_date: "2026-10-31",
    deadline_precision: "month",
    strength: "확약",
  },
  trend_sparkline: E1_SPARK,
  disclaimer: DISCLAIMER,
};

// ---------------------------------------------------------------------------
// Event 2: 누리군 새벽시장 상가 붕괴 사고 — 조치 완료 근거 확인, 기한 경과, 검토 필요
// ---------------------------------------------------------------------------

const E2_ARTICLES: ArticleOut[] = [
  article({
    news_id: "01100101.20251103083000011",
    title: "누리군 새벽시장 상가 일부 붕괴… 상인 3명 부상",
    provider: "연합뉴스",
    published_at: "2025-11-03",
    hilight: "3일 새벽 누리군 새벽시장 상가 건물 일부가 무너져 상인 3명이 다쳤다.",
    phase: "발생",
    link_method: "seed",
    link_score: 1,
    link_reason: "seed",
  }),
  article({
    news_id: "01100801.20251110090000012",
    title: "새벽시장 붕괴, 노후 철골 부식이 원인… 정밀안전진단 착수",
    provider: "한겨레",
    published_at: "2025-11-10",
    hilight: "누리군은 새벽시장 상가 붕괴 원인이 노후 철골 부식으로 확인됐다며 정밀안전진단에 착수했다고 밝혔다.",
    phase: "원인조사",
    link_score: 0.85,
    link_reason: "region+facility",
  }),
  article({
    news_id: "01100201.20251112180000013",
    title: "누리군수 “내년 7월까지 보강 공사 완료”",
    provider: "KBS",
    published_at: "2025-11-12",
    hilight: "누리군수는 12일 “정밀안전진단을 거쳐 내년 7월까지 보강 공사를 완료하겠다”고 말했다.",
    phase: "대응발표",
    link_score: 0.9,
    link_reason: "region+facility; actor=누리군",
  }),
  article({
    news_id: "01500601.20260305090000014",
    title: "새벽시장 상가 보강 공사 착공",
    provider: "국제신문",
    published_at: "2026-03-05",
    hilight: "누리군 새벽시장 상가 보강 공사가 5일 착공했다. 군은 7월 완공을 목표로 하고 있다.",
    phase: "조치진행",
    link_score: 0.87,
    link_reason: "region+facility",
  }),
  article({
    news_id: "01100101.20260828100000015",
    title: "누리군 새벽시장 상가 보강 공사 마무리… 재개장",
    provider: "연합뉴스",
    published_at: "2026-08-28",
    hilight: "누리군 새벽시장 상가가 보강 공사를 마치고 28일 재개장했다. 당초 목표보다 한 달가량 늦어졌다.",
    phase: "결과확인",
    link_score: 0.89,
    link_reason: "region+facility",
  }),
  article({
    news_id: "01100801.20260120090000016",
    title: "누리군, 전통시장 화재·붕괴 안전점검 강화",
    provider: "한겨레",
    published_at: "2026-01-20",
    hilight: "누리군이 관내 전통시장 6곳을 대상으로 화재·붕괴 위험 안전점검을 강화한다.",
    phase: "기타",
    link_method: "embedding",
    link_score: 0.66,
    link_reason: "embedding sim 0.66; region",
  }),
];

const E2_LOW_LINK_ARTICLE = article({
  news_id: "01500601.20260415090000017",
  title: "누리군 전통시장 주차장 확충 사업 추진",
  provider: "국제신문",
  published_at: "2026-04-15",
  hilight: "누리군이 전통시장 방문객 편의를 위해 공영주차장 확충 사업을 추진한다.",
  phase: "기타",
  link_method: "embedding",
  link_score: 0.41,
  link_reason: "region only",
});

const E2_PROMISES: PromiseOut[] = [
  {
    id: 5,
    actor_org: "누리군",
    actor_raw: "누리군수",
    action: "새벽시장 상가 정밀안전진단 후 보강 공사를 완료한다",
    deadline_raw: "내년 7월까지",
    deadline_date: "2026-07-31",
    deadline_precision: "month",
    strength: "확약",
    is_trackable: true,
    confidence: 0.88,
    deadline_passed: true,
    quotation: {
      news_id: "01100201.20251112180000013",
      source: "누리군수",
      quotation: "정밀안전진단을 거쳐 내년 7월까지 보강 공사를 완료하겠다",
      provider: "KBS",
      published_at: "2025-11-12",
    },
    evidence_article: E2_ARTICLES[2],
  },
];

const E2_SPARK = [23, 6, 2, 1, 0, 2, 1, 0, 1, 3, 0];

const E2_CARD: EventCard = {
  id: 2,
  title: "누리군 새벽시장 상가 붕괴 사고",
  incident_type: "사고>산업사고>붕괴",
  region: "누리군",
  facility: "새벽시장 상가",
  responsible_org: "누리군",
  occurred_at: "2025-11-03",
  status: "조치 완료 근거 확인",
  status_confidence: 0.82,
  priority_score: 61,
  needs_check: true,
  needs_review: true,
  deadline_date: "2026-07-31",
  deadline_passed_days: 42,
  last_followup_at: "2026-08-28",
  days_since_last_followup: 14,
  followup_count: 5,
  evidence_count: 4,
  article_count: 9,
  primary_promise: {
    actor_org: "누리군",
    action: "새벽시장 상가 정밀안전진단 후 보강 공사를 완료한다",
    deadline_date: "2026-07-31",
    deadline_precision: "month",
    strength: "확약",
  },
  trend_sparkline: E2_SPARK,
  disclaimer: DISCLAIMER,
};

// ---------------------------------------------------------------------------
// Event 3: 누리군 별빛마을 산사태 — 판단 불가 (추적 가능한 약속 없음)
// ---------------------------------------------------------------------------

const E3_ARTICLES: ArticleOut[] = [
  article({
    news_id: "01100101.20260320073000021",
    title: "누리군 별빛마을 산사태… 주택 2채 매몰, 주민 대피",
    provider: "연합뉴스",
    published_at: "2026-03-20",
    hilight: "20일 새벽 누리군 별빛마을 뒷산 사면이 무너져 주택 2채가 매몰되고 주민 15명이 대피했다.",
    phase: "발생",
    link_method: "seed",
    link_score: 1,
    link_reason: "seed",
  }),
  article({
    news_id: "01100801.20260324090000022",
    title: "별빛마을 산사태, 배수로 미정비 지적… 군 “조사 중”",
    provider: "한겨레",
    published_at: "2026-03-24",
    hilight: "별빛마을 산사태 원인으로 사면 배수로 미정비가 지적됐다. 누리군은 정확한 원인을 조사 중이라고 밝혔다.",
    phase: "원인조사",
    link_score: 0.83,
    link_reason: "region+facility",
  }),
  article({
    news_id: "01100201.20260325180000023",
    title: "누리군 “사면 안정화 방안 검토하겠다”",
    provider: "KBS",
    published_at: "2026-03-25",
    hilight: "누리군 관계자는 “전문가 자문을 거쳐 별빛마을 사면 안정화 방안을 검토하겠다”고 밝혔다.",
    phase: "대응발표",
    link_score: 0.8,
    link_reason: "region+facility; actor=누리군",
  }),
  article({
    news_id: "01500601.20260402090000024",
    title: "봄철 산사태 위험지역 점검 나선 누리군",
    provider: "국제신문",
    published_at: "2026-04-02",
    hilight: "누리군이 봄철 해빙기를 맞아 관내 산사태 위험지역 12곳에 대한 점검에 나섰다.",
    phase: "기타",
    link_method: "embedding",
    link_score: 0.58,
    link_reason: "embedding sim 0.58; region",
  }),
];

const E3_LOW_LINK_ARTICLE = article({
  news_id: "01500601.20260610090000025",
  title: "누리군 별빛마을 마을회관 신축 준공",
  provider: "국제신문",
  published_at: "2026-06-10",
  hilight: "누리군 별빛마을 마을회관이 신축 공사를 마치고 10일 준공식을 열었다.",
  phase: "기타",
  link_method: "embedding",
  link_score: 0.44,
  link_reason: "region+facility(마을명)",
});

const E3_PROMISES: PromiseOut[] = [
  {
    id: 7,
    actor_org: "누리군",
    actor_raw: "누리군 관계자",
    action: "별빛마을 사면 안정화 방안을 검토한다",
    deadline_raw: null,
    deadline_date: null,
    deadline_precision: "none",
    strength: "의사표명",
    is_trackable: false,
    confidence: 0.6,
    deadline_passed: false,
    quotation: {
      news_id: "01100201.20260325180000023",
      source: "누리군 관계자",
      quotation: "전문가 자문을 거쳐 별빛마을 사면 안정화 방안을 검토하겠다",
      provider: "KBS",
      published_at: "2026-03-25",
    },
    evidence_article: E3_ARTICLES[2],
  },
];

const E3_SPARK = [12, 3, 0, 0, 0, 0, 0];

const E3_CARD: EventCard = {
  id: 3,
  title: "누리군 별빛마을 산사태",
  incident_type: "재해>자연재해>산사태",
  region: "누리군",
  facility: "별빛마을 뒷산 사면",
  responsible_org: "누리군",
  occurred_at: "2026-03-20",
  status: "판단 불가",
  status_confidence: 0.3,
  priority_score: 55.2,
  needs_check: true,
  needs_review: false,
  deadline_date: null,
  deadline_passed_days: null,
  last_followup_at: "2026-04-02",
  days_since_last_followup: 162,
  followup_count: 1,
  evidence_count: 2,
  article_count: 4,
  primary_promise: null,
  trend_sparkline: E3_SPARK,
  disclaimer: DISCLAIMER,
};

// ---------------------------------------------------------------------------
// Assembly helpers
// ---------------------------------------------------------------------------

function monthLabels(startYm: string, count: number): string[] {
  let y = Number(startYm.slice(0, 4));
  let m = Number(startYm.slice(4, 6));
  const out: string[] = [];
  for (let i = 0; i < count; i += 1) {
    out.push(`${y}${String(m).padStart(2, "0")}`);
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

function buildTrend(startYm: string, spark: number[], card: EventCard): TrendOut {
  const labels = monthLabels(startYm, spark.length);
  const series = labels.map((label, i) => ({ label, hits: spark[i] }));
  const peakIdx = spark.indexOf(Math.max(...spark));
  const recent = spark.slice(-3);
  const recentMean = recent.reduce((a, b) => a + b, 0) / Math.max(recent.length, 1);
  const peak = spark[peakIdx] ?? 0;
  return {
    interval: "month",
    series,
    peak_label: labels[peakIdx] ?? null,
    peak_hits: peak,
    recent_mean: Number(recentMean.toFixed(2)),
    drop_ratio: peak > 0 ? Number((1 - recentMean / peak).toFixed(2)) : 0,
    markers: { occurred_at: card.occurred_at, deadline_date: card.deadline_date, today: TODAY },
  };
}

function buildTimeline(articles: ArticleOut[]): TimelinePhase[] {
  return PHASES.map((phase) => ({
    phase,
    articles: articles
      .filter((a) => a.phase === phase)
      .sort((a, b) => (a.published_at ?? "").localeCompare(b.published_at ?? "")),
  }));
}

interface MockEvent {
  card: EventCard;
  detail: EventDetailOut;
  evidence: EvidenceOut;
}

const E1: MockEvent = {
  card: E1_CARD,
  detail: {
    card: E1_CARD,
    summary:
      "2025년 7월 15일 집중호우로 가온시 달빛지하차도가 침수돼 차량 12대가 고립됐다. 가온시는 배수펌프 용량 부족을 원인으로 지목하고 2026년 10월까지 용량을 2배로 증설하겠다고 발표했다.",
    seed_query: '("달빛지하차도" OR "지하차도 침수") AND ("가온시" OR "가온시청")',
    status_reason: "R4_stale_followup",
    status_reason_text: "마지막 후속 기사가 60일 이전이고 완료·진행 신호가 없음",
    latest_judgment: {
      status: "후속보도 부족",
      reason: "R4_stale_followup",
      confidence: 0.5,
      unresolved_reason: null,
      judged_at: JUDGED_AT,
    },
    promises: E1_PROMISES,
    timeline: buildTimeline(E1_ARTICLES),
    trend: buildTrend("202507", E1_SPARK, E1_CARD),
    disclaimer: DISCLAIMER,
  },
  evidence: {
    event_id: 1,
    title: E1_CARD.title,
    status: "후속보도 부족",
    confidence: 0.5,
    rule: {
      name: "R4_stale_followup",
      text: "마지막 후속 기사가 60일 이전이고 완료·진행 신호가 없음",
      thresholds: { signal_min_conf: 0.6, completion_min_conf: 0.7, stale_followup_days: 60 },
    },
    unresolved_reason: "약속(배수펌프 증설)과 연결되는 완료·진행 신호가 기준 신뢰도 이상으로 확인되지 않음",
    signals: [
      {
        id: 21,
        signal: "진행",
        promise_id: 3,
        matches_promise: true,
        confidence: 0.48,
        evidence_span: "배수펌프 증설을 위한 설계 용역에 착수했다",
        rationale: "설계 착수는 진행 정황이나 공사 자체의 진행 여부는 확인되지 않음",
        counted: false,
        article: E1_ARTICLES[5],
      },
      {
        id: 22,
        signal: "무관",
        promise_id: 3,
        matches_promise: false,
        confidence: 0.71,
        evidence_span: "지하차도 침수 상황을 가정한 여름철 재난 대비 훈련을 실시했다",
        rationale: "약속 대상(배수펌프 증설)과 무관한 일반 훈련 보도",
        counted: false,
        article: E1_ARTICLES[7],
      },
    ],
    low_confidence_links: [{ article: E1_LOW_LINK_ARTICLE, link_score: 0.47, link_reason: "region only" }],
    last_followup_at: "2026-08-10",
    next_scheduled_run: NEXT_RUN,
    judged_at: JUDGED_AT,
    disclaimer: DISCLAIMER,
  },
};

const E2: MockEvent = {
  card: E2_CARD,
  detail: {
    card: E2_CARD,
    summary:
      "2025년 11월 3일 누리군 새벽시장 상가 건물 일부가 무너져 상인 3명이 다쳤다. 누리군은 노후 철골 부식을 원인으로 확인하고 2026년 7월까지 보강 공사를 완료하겠다고 발표했다.",
    seed_query: '("새벽시장" AND "붕괴") AND ("누리군" OR "누리군청")',
    status_reason: "R2_completion_evidence",
    status_reason_text: "완료 신호가 기준 신뢰도 이상으로 확인됨",
    latest_judgment: {
      status: "조치 완료 근거 확인",
      reason: "R2_completion_evidence",
      confidence: 0.82,
      unresolved_reason: null,
      judged_at: JUDGED_AT,
    },
    promises: E2_PROMISES,
    timeline: buildTimeline(E2_ARTICLES),
    trend: buildTrend("202511", E2_SPARK, E2_CARD),
    disclaimer: DISCLAIMER,
  },
  evidence: {
    event_id: 2,
    title: E2_CARD.title,
    status: "조치 완료 근거 확인",
    confidence: 0.82,
    rule: {
      name: "R2_completion_evidence",
      text: "완료 신호 1건(신뢰도 0.82)이 기준 0.7 이상",
      thresholds: { signal_min_conf: 0.6, completion_min_conf: 0.7, stale_followup_days: 60 },
    },
    unresolved_reason: null,
    signals: [
      {
        id: 11,
        signal: "완료",
        promise_id: 5,
        matches_promise: true,
        confidence: 0.82,
        evidence_span: "보강 공사를 마치고 28일 재개장했다",
        rationale: "완료형 표현과 약속 대상(보강 공사)이 일치",
        counted: true,
        article: E2_ARTICLES[4],
      },
      {
        id: 12,
        signal: "진행",
        promise_id: 5,
        matches_promise: true,
        confidence: 0.66,
        evidence_span: "보강 공사가 5일 착공했다",
        rationale: "착공은 약속 대상 공사의 진행 정황",
        counted: true,
        article: E2_ARTICLES[3],
      },
      {
        id: 13,
        signal: "무관",
        promise_id: 5,
        matches_promise: false,
        confidence: 0.9,
        evidence_span: "전통시장 6곳을 대상으로 화재·붕괴 위험 안전점검을 강화한다",
        rationale: "관내 일반 점검 보도로 약속 대상 시설과 직접 관련 없음",
        counted: false,
        article: E2_ARTICLES[5],
      },
    ],
    low_confidence_links: [{ article: E2_LOW_LINK_ARTICLE, link_score: 0.41, link_reason: "region only" }],
    last_followup_at: "2026-08-28",
    next_scheduled_run: NEXT_RUN,
    judged_at: JUDGED_AT,
    disclaimer: DISCLAIMER,
  },
};

const E3: MockEvent = {
  card: E3_CARD,
  detail: {
    card: E3_CARD,
    summary:
      "2026년 3월 20일 누리군 별빛마을 뒷산 사면이 무너져 주택 2채가 매몰되고 주민 15명이 대피했다. 누리군은 사면 안정화 방안을 검토하겠다고 밝혔으나 구체적인 기한은 제시하지 않았다.",
    seed_query: '("별빛마을" AND "산사태") AND ("누리군" OR "누리군청")',
    status_reason: "R5_unresolved",
    status_reason_text: "추적 가능한 약속이 없어 상태를 판정할 수 없음",
    latest_judgment: {
      status: "판단 불가",
      reason: "R5_unresolved",
      confidence: 0.3,
      unresolved_reason: "추적 가능한 약속이 추출되지 않음(발언 강도 ‘의사표명’만 확인)",
      judged_at: JUDGED_AT,
    },
    promises: E3_PROMISES,
    timeline: buildTimeline(E3_ARTICLES),
    trend: buildTrend("202603", E3_SPARK, E3_CARD),
    disclaimer: DISCLAIMER,
  },
  evidence: {
    event_id: 3,
    title: E3_CARD.title,
    status: "판단 불가",
    confidence: 0.3,
    rule: {
      name: "R5_unresolved",
      text: "추적 가능한 약속이 없어 상태를 판정할 수 없음",
      thresholds: { signal_min_conf: 0.6, completion_min_conf: 0.7, stale_followup_days: 60 },
    },
    unresolved_reason: "추적 가능한 약속이 추출되지 않음(발언 강도 ‘의사표명’만 확인)",
    signals: [
      {
        id: 31,
        signal: "불명확",
        promise_id: 7,
        matches_promise: false,
        confidence: 0.35,
        evidence_span: "산사태 위험지역 12곳에 대한 점검에 나섰다",
        rationale: "점검 활동이 별빛마을 사면 안정화와 연결되는지 불명확",
        counted: false,
        article: E3_ARTICLES[3],
      },
    ],
    low_confidence_links: [
      { article: E3_LOW_LINK_ARTICLE, link_score: 0.44, link_reason: "region+facility(마을명)" },
    ],
    last_followup_at: "2026-04-02",
    next_scheduled_run: NEXT_RUN,
    judged_at: JUDGED_AT,
    disclaimer: DISCLAIMER,
  },
};

const EVENTS: MockEvent[] = [E1, E2, E3];

function findEvent(id: number): MockEvent {
  const found = EVENTS.find((e) => e.card.id === id);
  if (!found) {
    throw new MockNotFound(`event ${id} not found`);
  }
  return found;
}

/** Thrown by mock fetchers for unknown ids; api.ts converts it into a 404 ApiError. */
export class MockNotFound extends Error {}

const delay = (ms = 150) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Mock endpoint implementations
// ---------------------------------------------------------------------------

export async function mockGetEvents(params: EventListParams = {}): Promise<EventListOut> {
  await delay();
  let items = EVENTS.map((e) => e.card);
  if (params.status) items = items.filter((c) => c.status === params.status);
  if (params.region) items = items.filter((c) => c.region === params.region);
  if (params.incident_type) items = items.filter((c) => c.incident_type.startsWith(params.incident_type as string));
  if (params.needs_check !== undefined) items = items.filter((c) => c.needs_check === params.needs_check);

  const sort = params.sort ?? "priority";
  items = [...items].sort((a, b) => {
    if (sort === "recent") return (b.last_followup_at ?? "").localeCompare(a.last_followup_at ?? "");
    if (sort === "occurred") return b.occurred_at.localeCompare(a.occurred_at);
    return b.priority_score - a.priority_score;
  });

  const page = Math.max(1, params.page ?? 1);
  const size = Math.min(100, Math.max(1, params.size ?? 20));
  const start = (page - 1) * size;

  return {
    items: items.slice(start, start + size),
    total: items.length,
    page,
    size,
    filters: {
      statuses: [...STATUSES],
      regions: Array.from(new Set(EVENTS.map((e) => e.card.region))),
      incident_types: Array.from(new Set(EVENTS.map((e) => e.card.incident_type))),
    },
  };
}

export async function mockGetEvent(id: number): Promise<EventDetailOut> {
  await delay();
  return findEvent(id).detail;
}

export async function mockGetEvidence(id: number): Promise<EvidenceOut> {
  await delay();
  return findEvent(id).evidence;
}

export async function mockGetTrend(id: number): Promise<TrendOut> {
  await delay();
  return findEvent(id).detail.trend;
}

let nextReportId = 7;
export async function mockPostReport(id: number, body: ReportIn): Promise<ReportOut> {
  await delay(400);
  findEvent(id);
  nextReportId += 1;
  return { id: nextReportId, event_id: id, type: body.type, created_at: new Date().toISOString() };
}

export async function mockGetReviewQueue(): Promise<ReviewQueueOut> {
  await delay();
  return {
    events: EVENTS.map((e) => e.card).filter((c) => c.needs_review),
    links: EVENTS.flatMap((e) =>
      e.evidence.low_confidence_links.map((l) => ({
        event_id: e.card.id,
        event_title: e.card.title,
        article: l.article,
        link_score: l.link_score,
        link_reason: l.link_reason,
      })),
    ),
    reports: [
      {
        id: 5,
        event_id: 1,
        event_title: E1_CARD.title,
        type: "wrong_link",
        news_id: E1_LOW_LINK_ARTICLE.news_id,
        comment: "다른 지하차도 기사입니다",
        created_at: "2026-09-11T15:02:11+09:00",
        resolved: false,
      },
      {
        id: 6,
        event_id: 3,
        event_title: E3_CARD.title,
        type: "wrong_status",
        news_id: null,
        comment: "군에서 4월에 사면 보강 계획을 공식 발표한 것으로 압니다.",
        created_at: "2026-09-10T11:20:00+09:00",
        resolved: true,
      },
    ],
  };
}

export async function mockPostAdminLink(
  eventId: number,
  newsId: string,
  action: AdminLinkAction,
): Promise<AdminLinkOut> {
  await delay(400);
  findEvent(eventId);
  return {
    event_id: eventId,
    news_id: newsId,
    link_method: "manual",
    confirmed: action === "confirm",
  };
}

export async function mockResolveReport(reportId: number): Promise<ResolveReportOut> {
  await delay(400);
  return { id: reportId, resolved: true };
}

export async function mockPostPipelineRun(body: PipelineRunIn): Promise<PipelineRunOut> {
  await delay(600);
  return {
    run_id: 12,
    stage: body.stage,
    ok: true,
    stats: { events: EVENTS.length, articles: 22, links: 20, promises: 4, signals: 6 },
    duration_ms: 1840,
  };
}

export async function mockGetMeta(): Promise<MetaOut> {
  await delay(50);
  return {
    service: "미결",
    bigkinds_mode: "mock",
    llm_mode: "stub",
    llm_model: "claude-opus-5",
    embedding_backend: "hash",
    event_count: EVENTS.length,
    today: TODAY,
    last_pipeline_run: {
      stage: "all",
      finished_at: "2026-09-11T14:59:31+09:00",
      ok: true,
      stats: { events: EVENTS.length, articles: 22, links: 20, promises: 4, signals: 6 },
    },
    disclaimer: DISCLAIMER,
  };
}

export async function mockGetHealth(): Promise<HealthOut> {
  return { ok: true };
}
