/**
 * Typed fetchers for every endpoint in docs/API_CONTRACT.md.
 *
 * - Server components call the backend directly: API_BASE_INTERNAL (default http://localhost:8000).
 * - Client components call relative `/api/...` on the same origin; next.config.ts rewrites
 *   `/api/:path*` to `${API_BASE_INTERNAL}/api/:path*`, so the browser never needs the backend URL.
 * - Admin-only routes (`/api/admin/*`, `/api/pipeline/*`) need `X-Admin-Token` when the backend sets ADMIN_TOKEN.
 *   On the server this file adds it from the server env; in the browser the Route Handler proxy adds it
 *   (src/lib/proxy.ts). The token is never shipped to the browser.
 * - NEXT_PUBLIC_USE_MOCK=1 switches every fetcher to src/lib/mock.ts (server and client).
 */
import * as mock from "./mock";
import type {
  AdminEventFlagOut,
  AdminLinkAction,
  AdminLinkOut,
  EventDetailOut,
  EventListOut,
  EventListParams,
  EvidenceOut,
  HealthOut,
  MetaOut,
  PipelineRunIn,
  PipelineRunOut,
  ReportIn,
  ReportOut,
  ResolveReportOut,
  ReviewQueueOut,
  TrendOut,
} from "./types";

/**
 * Backend base URL used by server-side code (server components, route handlers).
 * `||` (not `??`) so an empty env value falls back to the default. NEXT_PUBLIC_API_BASE is kept
 * as a legacy alias. Client components never use it (see `requestBase`).
 */
export const API_BASE = (
  process.env.API_BASE_INTERNAL ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:8000"
).replace(/\/+$/, "");
export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "1";

const REQUEST_TIMEOUT_MS = 10_000;
/** Server-only (never NEXT_PUBLIC_): sent as X-Admin-Token on admin/pipeline calls made from server components. */
const ADMIN_TOKEN = typeof window === "undefined" ? process.env.ADMIN_TOKEN || "" : "";

function isAdminPath(path: string): boolean {
  return path.startsWith("/api/admin/") || path.startsWith("/api/pipeline/");
}

/** Server: absolute backend URL. Browser: "" so paths stay relative and go through the Next.js rewrite. */
function requestBase(): string {
  return typeof window === "undefined" ? API_BASE : "";
}

export class ApiError extends Error {
  readonly status: number | undefined;
  readonly detail: string | undefined;
  /** true only when fetch itself failed (connection refused, DNS, timeout) — not for HTTP error bodies. */
  readonly network: boolean;

  constructor(message: string, status?: number, detail?: string, network = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.network = network;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/**
 * True only for the backend's own "event {id} not found" 404 (FastAPI `detail`).
 * Any other 404 (wrong route, proxy, missing sub-resource) is NOT treated as "event not found"
 * so pages can show an ErrorBox instead of a misleading 404 page.
 */
export function isEventNotFound(error: unknown, eventId: number): boolean {
  return isApiError(error) && error.status === 404 && error.detail === `event ${eventId} not found`;
}

/** Korean, user-facing message for any thrown value. */
export function describeError(error: unknown): { message: string; detail?: string } {
  if (isApiError(error)) return { message: error.message, detail: error.detail };
  if (error instanceof Error) return { message: "요청 처리 중 오류가 발생했습니다.", detail: error.message };
  return { message: "요청 처리 중 알 수 없는 오류가 발생했습니다." };
}

async function request<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const url = `${requestBase()}${path}`;
  const { json, ...rest } = init ?? {};
  const headers: Record<string, string> = { Accept: "application/json" };
  if (json !== undefined) headers["Content-Type"] = "application/json";
  if (ADMIN_TOKEN && isAdminPath(path)) headers["X-Admin-Token"] = ADMIN_TOKEN;

  let res: Response;
  try {
    res = await fetch(url, {
      ...rest,
      method: rest.method ?? (json !== undefined ? "POST" : "GET"),
      body: json !== undefined ? JSON.stringify(json) : rest.body,
      headers: { ...headers, ...(rest.headers as Record<string, string> | undefined) },
      cache: "no-store",
      signal: rest.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new ApiError("백엔드 서버에 연결할 수 없습니다.", undefined, reason, true);
  }

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
    const label =
      res.status === 404
        ? "요청한 자료를 찾을 수 없습니다."
        : res.status === 401
          ? "관리자 토큰이 없거나 올바르지 않습니다. 프런트 서버의 ADMIN_TOKEN 설정을 확인해 주세요."
          : res.status === 422
            ? "요청 형식이 올바르지 않습니다."
            : res.status === 403
              ? "허용되지 않은 요청입니다."
              : res.status === 409
                ? "이미 실행 중인 작업이 있습니다. 잠시 후 다시 시도해 주세요."
                : res.status === 502
                  ? "백엔드 서버에 연결할 수 없습니다."
                  : `백엔드 응답 오류 (HTTP ${res.status})`;
    throw new ApiError(label, res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Wrap a mock call so unknown ids behave like a backend 404. */
async function viaMock<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (error) {
    if (error instanceof mock.MockNotFound) throw new ApiError("요청한 자료를 찾을 수 없습니다.", 404, error.message);
    throw error;
  }
}

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** GET /api/events */
export function getEvents(params: EventListParams = {}): Promise<EventListOut> {
  if (USE_MOCK) return viaMock(() => mock.mockGetEvents(params));
  return request<EventListOut>(
    `/api/events${buildQuery({
      status: params.status,
      region: params.region,
      incident_type: params.incident_type,
      needs_check: params.needs_check,
      sort: params.sort,
      page: params.page,
      size: params.size,
    })}`,
  );
}

/** GET /api/events/{id} */
export function getEvent(id: number): Promise<EventDetailOut> {
  if (USE_MOCK) return viaMock(() => mock.mockGetEvent(id));
  return request<EventDetailOut>(`/api/events/${id}`);
}

/** GET /api/events/{id}/evidence */
export function getEvidence(id: number): Promise<EvidenceOut> {
  if (USE_MOCK) return viaMock(() => mock.mockGetEvidence(id));
  return request<EvidenceOut>(`/api/events/${id}/evidence`);
}

/** GET /api/events/{id}/trend */
export function getTrend(id: number): Promise<TrendOut> {
  if (USE_MOCK) return viaMock(() => mock.mockGetTrend(id));
  return request<TrendOut>(`/api/events/${id}/trend`);
}

/** POST /api/events/{id}/reports */
export function postReport(id: number, body: ReportIn): Promise<ReportOut> {
  if (USE_MOCK) return viaMock(() => mock.mockPostReport(id, body));
  return request<ReportOut>(`/api/events/${id}/reports`, { json: body });
}

/** GET /api/admin/review-queue */
export function getReviewQueue(): Promise<ReviewQueueOut> {
  if (USE_MOCK) return viaMock(() => mock.mockGetReviewQueue());
  return request<ReviewQueueOut>(`/api/admin/review-queue`);
}

/** POST /api/admin/links/{event_id}/{news_id} */
export function postAdminLink(eventId: number, newsId: string, action: AdminLinkAction): Promise<AdminLinkOut> {
  if (USE_MOCK) return viaMock(() => mock.mockPostAdminLink(eventId, newsId, action));
  return request<AdminLinkOut>(`/api/admin/links/${eventId}/${encodeURIComponent(newsId)}`, {
    json: { action },
  });
}

/** POST /api/admin/reports/{report_id}/resolve */
export function resolveReport(reportId: number): Promise<ResolveReportOut> {
  if (USE_MOCK) return viaMock(() => mock.mockResolveReport(reportId));
  return request<ResolveReportOut>(`/api/admin/reports/${reportId}/resolve`, { method: "POST" });
}

/** POST /api/admin/events/{event_id}/llm-retry — clear LLM failure counters and retry on the next run */
export function llmRetry(eventId: number): Promise<AdminEventFlagOut> {
  if (USE_MOCK) return viaMock(() => mock.mockAdminEventFlag(eventId));
  return request<AdminEventFlagOut>(`/api/admin/events/${eventId}/llm-retry`, { method: "POST" });
}

/** POST /api/admin/events/{event_id}/llm-ack — acknowledge gave-up LLM items (leave the review queue) */
export function llmAck(eventId: number): Promise<AdminEventFlagOut> {
  if (USE_MOCK) return viaMock(() => mock.mockAdminEventFlag(eventId));
  return request<AdminEventFlagOut>(`/api/admin/events/${eventId}/llm-ack`, { method: "POST" });
}

/** POST /api/admin/events/{event_id}/clear-change-flag — tracker review done */
export function clearChangeFlag(eventId: number): Promise<AdminEventFlagOut> {
  if (USE_MOCK) return viaMock(() => mock.mockAdminEventFlag(eventId));
  return request<AdminEventFlagOut>(`/api/admin/events/${eventId}/clear-change-flag`, { method: "POST" });
}

/** POST /api/pipeline/run (dev/demo only; 403 when PIPELINE_API_ENABLED=false, 409 while a run is in progress, 404 for unknown event_id) */
export function postPipelineRun(body: PipelineRunIn): Promise<PipelineRunOut> {
  if (USE_MOCK) return viaMock(() => mock.mockPostPipelineRun(body));
  return request<PipelineRunOut>(`/api/pipeline/run`, { json: body });
}

/** GET /api/meta */
export function getMeta(): Promise<MetaOut> {
  if (USE_MOCK) return viaMock(() => mock.mockGetMeta());
  return request<MetaOut>(`/api/meta`);
}

/** GET /api/health */
export function getHealth(): Promise<HealthOut> {
  if (USE_MOCK) return viaMock(() => mock.mockGetHealth());
  return request<HealthOut>(`/api/health`);
}

// ---------------------------------------------------------------------------
// Result helper for server components: never throw into the route.
// ---------------------------------------------------------------------------

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

export async function safe<T>(promise: Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, data: await promise };
  } catch (error) {
    if (isApiError(error)) return { ok: false, error };
    const { message, detail } = describeError(error);
    return { ok: false, error: new ApiError(message, undefined, detail) };
  }
}
