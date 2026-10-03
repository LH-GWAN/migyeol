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
export type PipelineStage = "discover" | "collect" | "link" | "extract" | "judge" | "all";
export type BigKindsMode = "mock" | "live";

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
}

export interface TrendPoint {
  label: string; // YYYYMM
  hits: number;
}

export interface TrendMarkers {
  occurred_at: string | null;
  deadline_date: string | null;
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

export interface EventCard {
  id: number;
  title: string;
  incident_type: string;
  region: string;
  facility: string | null;
  responsible_org: string;
  occurred_at: string;
  status: EventStatus;
  status_confidence: number;
  priority_score: number;
  needs_check: boolean;
  needs_review: boolean;
  deadline_date: string | null;
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
  status_reason: string | null;
  status_reason_text: string;
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
  evidence_span: string;
  rationale: string;
  counted: boolean;
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
  confidence: number;
  rule: RuleOut;
  unresolved_reason: string | null;
  signals: SignalOut[];
  low_confidence_links: LowConfidenceLink[];
  last_followup_at: string | null;
  next_scheduled_run: string | null;
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
  reports: ReviewReport[];
}

// ---------- POST /api/admin/links/{event_id}/{news_id} ----------

export type AdminLinkAction = "confirm" | "reject";

export interface AdminLinkIn {
  action: AdminLinkAction;
}

export interface AdminLinkOut {
  event_id: number;
  news_id: string;
  link_method: LinkMethod;
  confirmed: boolean;
}

// ---------- POST /api/admin/reports/{report_id}/resolve ----------

export interface ResolveReportOut {
  id: number;
  resolved: boolean;
}

// ---------- POST /api/pipeline/run ----------

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
  stats: PipelineStats;
  duration_ms: number;
}

// ---------- GET /api/meta ----------

export interface LastPipelineRun {
  stage: PipelineStage;
  finished_at: string;
  ok: boolean;
  stats: PipelineStats;
}

export interface MetaOut {
  service: string;
  bigkinds_mode: BigKindsMode;
  llm_mode: string;
  llm_model: string;
  embedding_backend: string;
  event_count: number;
  today: string;
  last_pipeline_run: LastPipelineRun | null;
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
