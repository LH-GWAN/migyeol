/**
 * v2 §7.3 공공기관 게시판 확인 API 타입 + fetch (트랙 C). 서버 컴포넌트(근거 화면)에서 부른다.
 */
import { getJson } from "./press";

/** found=기관 발표 확인 / not_triggered=트리거 조건 미충족 / no_site=등록된 게시판 없음 / not_found=확인했으나 없음 / not_checked=아직 확인 전 / error=확인 중 오류 */
export type NoticeState = "found" | "not_triggered" | "no_site" | "not_found" | "not_checked" | "error";

export interface OrgNoticeItem {
  title: string;
  published_at: string | null;
  url: string;
  snippet: string;
  match_score: number;
  matched_terms: string[];
  site_name: string;
}

export interface EventNoticesOut {
  event_id: number;
  org: string | null;
  triggered: boolean;
  /** true only when the event is still 후속보도 부족·판단 불가 AND a notice was posted after the latest source statement — then "뉴스에는 없었지만" wording applies */
  news_gap: boolean;
  news_gap_items: number;
  reason: string;
  basis_date: string | null;
  trigger_days: number;
  state: NoticeState;
  state_label: string;
  checked_at: string | null;
  sites: Array<{ org: string; name: string; url: string }>;
  items: OrgNoticeItem[];
  note: string;
  disclaimer: string;
}

/** GET /api/notices/events/{id} */
export function getEventNotices(eventId: number): Promise<EventNoticesOut> {
  return getJson<EventNoticesOut>(`/api/notices/events/${eventId}`);
}
