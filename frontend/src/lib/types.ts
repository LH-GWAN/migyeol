/**
 * TypeScript types derived verbatim from docs/API_CONTRACT.md (v0, Phase 0).
 * Field names and enum strings must match the backend exactly.
 */

// ---------- Enums / literal unions ----------

export const STATUSES = [
  "조치 완료 근거 확인",
  "조치 진행 정황 확인",
  "새로운 문제 발생",
  "후속보도 부족",
  "판단 불가",
] as const;
export type EventStatus = (typeof STATUSES)[number];

export const PHASES = ["발생", "원인조사", "대응발표", "조치진행", "결과확인", "기타"] as const;
export type Phase = (typeof PHASES)[number];

export type LinkMethod = "seed" | "rule" | "embedding" | "manual";
export type ChangeStatus = "ok" | "updated" | "cancelled";
export type Strength = "확약" | "계획" | "의사표명" | "해당없음";
export type DeadlinePrecision = "day" | "month" | "year" | "none";
export type SignalKind = "완료" | "진행" | "새로운문제" | "무관" | "불명확";
export type ReportType = "wrong_link" | "wrong_status" | "wrong_promise" | "other";
export type SortKey = "priority" | "recent" | "occurred";
export type PipelineStage = "discover" | "collect" | "link" | "extract" | "judge" | "tracker" | "all";
/** mock = 가상 데이터, record = 녹화된 실제 응답 재생, live = 실시간 빅카인즈 API */
export type BigKindsMode = "mock" | "record" | "live";

export const SORT_KEYS: readonly SortKey[] = ["priority", "recent", "occurred"];

// ---------- Common objects ----------

export interface ArticleOut {
  news_id: string;
  title: string;
  provider: string;
  published_at: string | null; // YYYY-MM-DD
  provider_link_page: string | null;
  hilight: string; // <= 200 chars, never full text
  byline: string | null;
  // link 필드는 사건에 연결되지 않은 기사(예: 약속 근거 기사가 연결 목록에 없을 때)면 null
  phase: Phase | null;
  link_method: LinkMethod | null;
  link_score: number | null;
  link_reason: string | null;
  change_status: ChangeStatus;
}

export interface QuotationOut {
  news_id: string;
  source: string;
  quotation: string;
  provider: string;
  published_at: string | null;
}

export interface PromiseOut {
  id: number;
  actor_org: string;
  actor_raw: string;
  action: string;
  deadline_raw: string | null;
  deadline_date: string | null;
  deadline_precision: DeadlinePrecision;
  strength: Strength;
  is_trackable: boolean;
  confidence: number;
  deadline_passed: boolean;
  quotation: QuotationOut | null;
  evidence_article: ArticleOut | null;
  /** 중복 병합된 대표 약속 id. 병합된 약속은 백엔드가 목록에서 이미 제외한다. */
  merged_into_id: number | null;
  /** news_ids of the duplicate promises merged into this one (their evidence articles) */
  merged_news_ids: string[];
}

export interface TrendPoint {
  label: string; // YYYYMM
  hits: number;
}

export interface TrendMarkers {
  occurred_at: string | null;
  deadline_date: string | null;
  /** precision of `deadline_date` (null when there is no deadline) */
  deadline_precision: DeadlinePrecision | null;
  today: string;
}

export interface TrendOut {
  interval: "month";
  series: TrendPoint[];
  peak_label: string | null;
  peak_hits: number;
  recent_mean: number;
  drop_ratio: number;
  markers: TrendMarkers;
}

export interface PrimaryPromise {
  actor_org: string;
  action: string;
  deadline_date: string | null;
  deadline_precision: DeadlinePrecision;
  strength: Strength;
}

/**
 * EventCard. `deadline_date` and `deadline_passed_days` always refer to the SAME promise
 * (the overdue one if any, otherwise the firm one).
 */
export interface EventCard {
  id: number;
  title: string;
  incident_type: string;
  region: string;
  facility: string | null;
  responsible_org: string;
  occurred_at: string;
  status: EventStatus;
  /** null until a judgment has run (`judged` false) */
  status_confidence: number | null;
  /** false until judge has run at least once — then status is the model default, not a judgment */
  judged: boolean;
  priority_score: number;
  needs_check: boolean;
  needs_review: boolean;
  /** followup articles still waiting for LLM classification (call cap / failures) — >0 means the judgment is provisional */
  pending_followups: number;
  /** public quotations still waiting for promise extraction — >0 means the judgment is provisional */
  pending_quotations: number;
  /** an item failed LLM twice and was recorded as 불명확/해당없음; admin can retry or acknowledge */
  llm_gave_up: boolean;
  /** admin acknowledged the gave-up items (cleared from the review queue until a new failure) */
  llm_acked: boolean;
  deadline_date: string | null;
  /** precision of `deadline_date` (null when there is no deadline) */
  deadline_precision: DeadlinePrecision | null;
  deadline_passed_days: number | null;
  last_followup_at: string | null;
  days_since_last_followup: number | null;
  followup_count: number;
  evidence_count: number;
  article_count: number;
  primary_promise: PrimaryPromise | null;
  trend_sparkline: number[];
  disclaimer: string;
}

// ---------- GET /api/events ----------

export interface EventListParams {
  status?: EventStatus;
  region?: string;
  incident_type?: string;
  needs_check?: boolean;
  /** v2: 정규화 기관 키 */
  org?: string;
  /** v2: true = 후속보도 확인됨(완료·진행·새로운 문제) */
  followup?: boolean;
  sort?: SortKey;
  page?: number;
  size?: number;
}

export interface EventListFilters {
  statuses: EventStatus[];
  regions: string[];
  incident_types: string[];
}

export interface EventListOut {
  items: EventCard[];
  total: number;
  page: number;
  size: number;
  filters: EventListFilters;
}

// ---------- GET /api/events/{id} ----------

export interface LatestJudgment {
  status: EventStatus;
  reason: string;
  confidence: number;
  unresolved_reason: string | null;
  judged_at: string;
}

export interface TimelinePhase {
  phase: Phase;
  articles: ArticleOut[];
}

export interface EventDetailOut {
  card: EventCard;
  summary: string;
  seed_query: string;
  /** null when no judgment has run yet (then `status_reason_text` = "아직 상태 판정이 실행되지 않음") */
  status_reason: string | null;
  status_reason_text: string;
  /** null when no judgment has run yet */
  latest_judgment: LatestJudgment | null;
  promises: PromiseOut[];
  timeline: TimelinePhase[];
  trend: TrendOut;
  disclaimer: string;
}

// ---------- GET /api/events/{id}/evidence ----------

export interface RuleOut {
  name: string;
  text: string;
  thresholds: Record<string, number>;
}

export interface SignalOut {
  id: number;
  signal: SignalKind;
  promise_id: number | null;
  matches_promise: boolean;
  confidence: number;
  evidence_span: string; // may be empty for 무관·불명확
  rationale: string;
  /** adopted as evidence by the applied rule (R1~R5). NOT "matches and above threshold". */
  counted: boolean;
  /** R5 reference signal: looked at by the rule but not adopted */
  considered: boolean;
  /** R1 only: completion evidence that satisfied R2 before the newer 새로운문제 signal (shown, not adopted) */
  prior_done?: boolean;
  /** false when the article link was rejected / under review / cancelled */
  active: boolean;
  article: ArticleOut | null; // 신호의 기사가 DB에 없으면 null
}

export interface LowConfidenceLink {
  article: ArticleOut;
  link_score: number;
  link_reason: string;
}

export interface EvidenceOut {
  event_id: number;
  title: string;
  status: EventStatus;
  /** false until a judgment has run */
  judged: boolean;
  /** null until a judgment has run */
  confidence: number | null;
  rule: RuleOut;
  unresolved_reason: string | null;
  signals: SignalOut[];
  low_confidence_links: LowConfidenceLink[];
  last_followup_at: string | null;
  /** null when the scheduler is off */
  next_scheduled_run: string | null;
  /** null when no judgment has run yet */
  judged_at: string | null;
  disclaimer: string;
}

// ---------- POST /api/events/{id}/reports ----------

export interface ReportIn {
  type: ReportType;
  news_id?: string;
  comment?: string;
}

export interface ReportOut {
  id: number;
  event_id: number;
  type: ReportType;
  created_at: string;
}

// ---------- GET /api/admin/review-queue ----------

export interface ReviewLink {
  event_id: number;
  event_title: string;
  article: ArticleOut;
  link_score: number;
  link_reason: string;
}

export interface ReviewReport {
  id: number;
  event_id: number;
  event_title: string;
  type: ReportType;
  news_id: string | null;
  comment: string | null;
  created_at: string;
  resolved: boolean;
}

export interface ReviewQueueOut {
  events: EventCard[];
  links: ReviewLink[];
  /** links an admin rejected; can be restored with action "restore" */
  rejected_links: ReviewLink[];
  reports: ReviewReport[];
}

// ---------- POST /api/admin/links/{event_id}/{news_id} ----------

export type AdminLinkAction = "confirm" | "reject" | "restore";

export interface AdminLinkIn {
  action: AdminLinkAction;
}

export interface AdminLinkOut {
  event_id: number;
  news_id: string;
  link_method: LinkMethod;
  confirmed: boolean;
  rejected: boolean;
}

// ---------- POST /api/admin/reports/{report_id}/resolve ----------

export interface ResolveReportOut {
  id: number;
  resolved: boolean;
}

/** POST /api/admin/events/{id}/llm-retry | llm-ack | clear-change-flag */
export interface AdminEventFlagOut {
  event_id: number;
  needs_review: boolean;
  removed?: number;
}

// ---------- POST /api/pipeline/run ----------
// 409 when a run is already in progress, 404 for an unknown event_id.

export interface PipelineRunIn {
  stage: PipelineStage;
  event_id: number | null;
}

export interface PipelineStats {
  events?: number;
  articles?: number;
  links?: number;
  promises?: number;
  signals?: number;
  [key: string]: number | undefined;
}

export interface PipelineRunOut {
  run_id: number;
  stage: PipelineStage;
  ok: boolean;
  stats: PipelineStats; // always a numeric summary object
  duration_ms: number;
}

// ---------- GET /api/meta ----------

/** Last successful full refresh (stage all/judge, all events). */
export interface LastPipelineRun {
  stage: PipelineStage;
  event_id: number | null;
  finished_at: string | null;
  ok: boolean;
  error_summary: string | null;
  stats: PipelineStats;
}

/** Most recent run of any kind, success or not. `error_summary` is a category label only (never the raw server message). */
export interface LastRun {
  stage: PipelineStage;
  /** null while the run is still in progress */
  finished_at: string | null;
  ok: boolean;
  error_summary: string | null;
  event_id: number | null;
  stats: PipelineStats;
  /** collection warnings (time_line fallback, empty override query …), at most 20 */
  warnings?: string[];
  /** per-event failures isolated inside the run */
  failures?: number;
}

export interface MetaOut {
  service: string;
  bigkinds_mode: BigKindsMode;
  llm_mode: string;
  llm_model: string;
  embedding_backend: string;
  event_count: number;
  today: string;
  /** true when the backend pins "today" (TODAY_OVERRIDE) instead of using the clock */
  today_overridden: boolean;
  scheduler_enabled: boolean;
  /** configuration warnings from the backend (e.g. TODAY_OVERRIDE left in the past) */
  warnings: string[];
  last_pipeline_run: LastPipelineRun | null;
  last_run: LastRun | null;
  disclaimer: string;
}

// ---------- GET /api/health ----------

export interface HealthOut {
  ok: boolean;
}

// ---------- Error body (FastAPI default) ----------

export interface ApiErrorBody {
  detail: string | unknown;
}

// ---------- Helpers ----------

export function isEventStatus(value: unknown): value is EventStatus {
  return typeof value === "string" && (STATUSES as readonly string[]).includes(value);
}

export function isSortKey(value: unknown): value is SortKey {
  return typeof value === "string" && (SORT_KEYS as readonly string[]).includes(value);
}
