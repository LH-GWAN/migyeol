/**
 * Typed fetchers for every endpoint in docs/API_CONTRACT.md.
 *
 * - Base URL: NEXT_PUBLIC_API_BASE (default http://localhost:8000)
 * - NEXT_PUBLIC_USE_MOCK=1 switches every fetcher to src/lib/mock.ts
 * - Works in both server components and client components (NEXT_PUBLIC_* is inlined).
 */
import * as mock from "./mock";
import type {
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
  ReviewQueueOut,
  TrendOut,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "1";

const REQUEST_TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  readonly status: number | undefined;
  readonly detail: string | undefined;

  constructor(message: string, status?: number, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Korean, user-facing message for any thrown value. */
export function describeError(error: unknown): { message: string; detail?: string } {
  if (isApiError(error)) return { message: error.message, detail: error.detail };
  if (error instanceof Error) return { message: "요청 처리 중 오류가 발생했습니다.", detail: error.message };
  return { message: "요청 처리 중 알 수 없는 오류가 발생했습니다." };
}

async function request<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const url = `${API_BASE}${path}`;
  const { json, ...rest } = init ?? {};
  const headers: Record<string, string> = { Accept: "application/json" };
  if (json !== undefined) headers["Content-Type"] = "application/json";

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
    throw new ApiError(`백엔드 서버에 연결할 수 없습니다. (${API_BASE})`, undefined, reason);
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
        : res.status === 422
          ? "요청 형식이 올바르지 않습니다."
          : res.status === 403
            ? "허용되지 않은 요청입니다."
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

/** POST /api/pipeline/run (dev/demo only; 403 when PIPELINE_API_ENABLED=false) */
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
