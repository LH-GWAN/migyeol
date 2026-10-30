/**
 * v2 §7.1 대시보드 집계 API 타입 + fetch (트랙 B).
 *
 * lib/api.ts 의 패턴(API_BASE / ApiError / safe)을 그대로 따른다. 대시보드는 서버 컴포넌트에서만 부르므로
 * 서버 쪽 절대 URL(API_BASE)로 직접 호출한다. NEXT_PUBLIC_USE_MOCK=1 이면 집계 API 가 없으므로 오류로 표시한다.
 */
import { API_BASE, ApiError, getEvents, USE_MOCK } from "./api";
import type { EventListOut, EventStatus, SortKey } from "./types";

// ---------- 실험 리포트 (§4.6 experiment_latest.json, 키 이름 고정) ----------

export interface ExperimentEvents {
  total: number;
  with_followup: number;
  followup_rate: number;
  by_status: Record<string, number>;
  with_org_notice: number;
  articles_per_event: { median: number; max: number };
}

export interface ExperimentSuccess {
  min_followup_events: number;
  target_followup_events: number;
  met_min: boolean;
  met_target: boolean;
}

export interface ExperimentLabels {
  labeled: number;
  followup_precision: number | null;
  followup_recall: number | null;
  cluster_precision: number | null;
  org_accuracy: number | null;
}

/** summary.experiment — experiment_latest.json 에서 큰 목록(by_org·by_incident_type·timeseries·daily)을 뺀 것 */
export interface ExperimentSummary {
  generated_at: string;
  as_of: string;
  bigkinds_mode: string;
  seeds_file?: string;
  embedding_backend?: string;
  llm_mode?: string;
  source_articles: { collected: number; unique: number };
  events: ExperimentEvents;
  windows_days: Record<string, number>;
  signals: Record<string, number>;
  labels?: ExperimentLabels;
  cost?: { api_calls: Record<string, number>; elapsed_sec: number; recordings: number };
  success?: ExperimentSuccess;
}

export interface ExperimentReport extends ExperimentSummary {
  by_incident_type: Array<{ incident_type: string; events: number; with_followup: number; rate: number }>;
  by_org: OrgRow[];
  timeseries: Array<{ bucket: string; occurred: number; followup_articles: number; dead: number | null; injured: number | null }>;
  daily: Array<{ date: string; occurred: number; followup_articles: number }>;
}

// ---------- /api/dashboard/* ----------

export interface DashboardSummary {
  events_total: number;
  /** 유효 후속 신호(완료·진행·새로운문제, 기준 신뢰도 이상)가 1건 이상인 사건 수 */
  with_followup: number;
  followup_rate: number;
  /** 기한이 지났고 '조치 완료 근거 확인' 이 아닌 사건 수 */
  overdue: number;
  with_org_notice: number;
  followup_articles: number;
  orgs: number;
  by_status: Record<EventStatus, number>;
  /** 마지막으로 성공한 전체 갱신 시각 (없으면 null) */
  last_run: string | null;
  as_of: string;
  bigkinds_mode: string;
  experiment: ExperimentSummary | null;
  disclaimer: string;
}

export type Interval = "month" | "day";

export interface TimeseriesRow {
  bucket: string; // YYYY-MM | YYYY-MM-DD
  occurred: number;
  followup_articles: number;
  /** 유효 신호가 붙은 후속 기사 수 */
  confirmed_followups: number;
  dead: number | null;
  injured: number | null;
}

export interface IncidentRow {
  incident_type: string;
  label: string;
  events: number;
  with_followup: number;
  rate: number;
  subtypes: string[];
}

export type OrgSort = "followup" | "rate" | "events";

export interface OrgRow {
  org: string;
  events: number;
  with_followup: number;
  rate: number;
  latest_followup_at: string | null;
  last_article_at?: string | null;
  confirmed_signals?: number;
  event_ids: number[];
  /** 정규화 전 원래 기관명들 */
  raw_names?: string[];
}

export interface RecentFollowup {
  event_id: number;
  event_title: string;
  event_status: EventStatus;
  org: string;
  signal: "완료" | "진행" | "새로운문제";
  confidence: number;
  evidence_span: string;
  news_id: string;
  title: string;
  provider: string;
  published_at: string | null;
  link: string | null;
}

export type NoticeCaseKind = "notice_without_followup" | "notice_before_followup";

export interface NoticeCaseNotice {
  title: string;
  published_at: string | null;
  url: string;
  site_key: string;
  site_name: string;
  site_url: string;
  snippet: string;
  match_score: number;
  matched_terms: string[];
}

/** 기관 공식 게시판에서 발표가 확인된 사례 (보조 근거 — 상태 5종을 바꾸지 않는다) */
export interface NoticeCase {
  kind: NoticeCaseKind;
  kind_label: string;
  event_id: number;
  title: string;
  status: EventStatus;
  org: string;
  incident_type: string | null;
  occurred_at: string | null;
  /** 마지막 연결 기사일 */
  last_article_at: string | null;
  /** 기관 발언 이후 연결 기사 수 (후속보도로 확인된 것만이 아니라 전체) */
  articles_after: number;
  latest_confirmed_at: string | null;
  notices_count: number;
  notice: NoticeCaseNotice;
  /** without: 발생 → 기관 발표, before: 기관 발표 → 다음 후속보도 (일) */
  gap_days: number | null;
  /** before: 발표 뒤 처음 확인된 후속보도일 */
  followup_after_at: string | null;
}

export interface NoticeCasesOut {
  items: NoticeCase[];
  kinds: Record<NoticeCaseKind, string>;
  disclaimer: string;
}

const REQUEST_TIMEOUT_MS = 10_000;

async function get<T>(path: string, opts: { nullOn404?: boolean } = {}): Promise<T> {
  if (USE_MOCK) throw new ApiError("프런트 목 데이터 모드에서는 대시보드 집계를 제공하지 않습니다.", undefined, path);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new ApiError("백엔드 서버에 연결할 수 없습니다.", undefined, reason, true);
  }
  if (res.status === 404 && opts.nullOn404) return null as T;
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(
      res.status === 404 ? "요청한 자료를 찾을 수 없습니다." : `백엔드 응답 오류 (HTTP ${res.status})`,
      res.status,
      detail,
    );
  }
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** GET /api/dashboard/summary */
export function getDashboardSummary(): Promise<DashboardSummary> {
  return get<DashboardSummary>("/api/dashboard/summary");
}

/** GET /api/dashboard/timeseries — 빈 구간도 0 으로 채워 온다 */
export function getTimeseries(interval: Interval = "month", from?: string, until?: string): Promise<TimeseriesRow[]> {
  return get<TimeseriesRow[]>(`/api/dashboard/timeseries${qs({ interval, from, until })}`);
}

/** GET /api/dashboard/by-incident */
export function getByIncident(): Promise<IncidentRow[]> {
  return get<IncidentRow[]>("/api/dashboard/by-incident");
}

/** GET /api/dashboard/by-org (기본: 후속보도 확인 건수 내림차순) */
export function getByOrg(sort: OrgSort = "followup"): Promise<OrgRow[]> {
  return get<OrgRow[]>(`/api/dashboard/by-org${qs({ sort })}`);
}

/** GET /api/dashboard/recent-followups */
export function getRecentFollowups(limit = 10): Promise<RecentFollowup[]> {
  return get<RecentFollowup[]>(`/api/dashboard/recent-followups${qs({ limit })}`);
}

/** GET /api/dashboard/notice-cases — 종류별 per_kind 건 */
export function getNoticeCases(perKind = 1): Promise<NoticeCasesOut> {
  return get<NoticeCasesOut>(`/api/dashboard/notice-cases${qs({ per_kind: perKind })}`);
}

/** GET /api/dashboard/experiment — 리포트가 아직 없으면(404) null */
export function getExperiment(): Promise<ExperimentReport | null> {
  return get<ExperimentReport | null>("/api/dashboard/experiment", { nullOn404: true });
}

/**
 * GET /api/events with the v2 `org` query (탭·기관 필터용).
 * lib/api.ts 의 getEvents 는 org 를 보내지 않으므로 여기서 직접 부른다. 백엔드가 org 를 아직 모르면 무시된다
 * (프런트에서 따로 거르지 않는다 — 통합 요청 참고).
 */
export interface EventQuery {
  status?: EventStatus;
  needs_check?: boolean;
  org?: string;
  sort?: SortKey;
  page?: number;
  size?: number;
  region?: string;
  incident_type?: string;
}

export function getEventsWithOrg(p: EventQuery): Promise<EventListOut> {
  if (USE_MOCK) {
    // 프런트 목 데이터에는 기관 필터가 없다 — 나머지 조건만 기존 fetcher 로
    const { org: _org, ...rest } = p; // eslint-disable-line @typescript-eslint/no-unused-vars
    return getEvents(rest);
  }
  return get<EventListOut>(
    `/api/events${qs({
      status: p.status,
      region: p.region,
      incident_type: p.incident_type,
      needs_check: p.needs_check,
      org: p.org,
      sort: p.sort,
      page: p.page,
      size: p.size,
    })}`,
  );
}

/** 신호 → 시민용 문구 */
export const SIGNAL_LABEL: Record<RecentFollowup["signal"], string> = {
  완료: "조치 완료 보도",
  진행: "조치 진행 보도",
  새로운문제: "새로운 문제 보도",
};
