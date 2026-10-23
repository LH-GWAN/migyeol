/**
 * v2 §7.2 보도자료 초안 API 타입 + fetch (트랙 C).
 *
 * lib/api.ts 의 패턴(API_BASE / ApiError / safe)을 따른다. /press 는 서버 컴포넌트에서 부르므로 서버 쪽 절대 URL(API_BASE)로
 * 직접 호출한다. NEXT_PUBLIC_USE_MOCK=1 이면 초안 API 가 없으므로 오류로 표시한다.
 */
import { API_BASE, ApiError, USE_MOCK } from "./api";

export interface PressSection {
  heading: string;
  body: string;
}

export interface PressSource {
  ref: number;
  news_id: string;
  title: string;
  provider: string;
  published_at: string | null;
  link: string | null;
}

export interface PressDraft {
  scope: "all" | "event";
  event_id: number | null;
  title: string;
  markdown: string;
  sections: PressSection[];
  generated_at: string;
  sources: PressSource[];
  disclaimer: string;
  header_note: string;
  /** template-v1 | llm-press-v1(<model>) */
  writer: string;
  stats: Record<string, number | string>;
  /** 전체 초안만: 숫자 출처 (db | experiment) */
  data_source?: "db" | "experiment";
  experiment_generated_at?: string | null;
}

export interface PressEventRow {
  id: number;
  title: string;
  status: string | null;
  org: string;
  occurred_at: string;
  has_followup: boolean;
  notice_found: boolean;
}

const REQUEST_TIMEOUT_MS = 15_000;

/** Server-side GET against the backend (same error semantics as lib/api.ts). */
export async function getJson<T>(path: string): Promise<T> {
  if (USE_MOCK) throw new ApiError("프런트 목 데이터 모드에서는 이 자료를 제공하지 않습니다.", undefined, path);
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
      res.status === 404
        ? "요청한 자료를 찾을 수 없습니다."
        : res.status === 422
          ? "요청 형식이 올바르지 않습니다."
          : `백엔드 응답 오류 (HTTP ${res.status})`,
      res.status,
      detail,
    );
  }
  return (await res.json()) as T;
}

/** GET /api/press/draft (eventId 가 있으면 사건별 초안) */
export function getPressDraft(eventId?: number | null): Promise<PressDraft> {
  return getJson<PressDraft>(eventId ? `/api/press/draft?event_id=${eventId}` : "/api/press/draft");
}

/** GET /api/press/events — 사건별 초안 선택 목록 */
export function getPressEvents(): Promise<PressEventRow[]> {
  return getJson<PressEventRow[]>("/api/press/events");
}

/** Markdown 다운로드 파일 이름: migyeol_press_all_20260911.md / migyeol_press_event3_20260911.md */
export function draftFileName(draft: Pick<PressDraft, "scope" | "event_id" | "generated_at">): string {
  const day = (draft.generated_at || "").slice(0, 10).replace(/-/g, "") || "draft";
  return draft.scope === "event" ? `migyeol_press_event${draft.event_id}_${day}.md` : `migyeol_press_all_${day}.md`;
}
