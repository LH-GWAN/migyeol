/**
 * 민간 지원 소식 API 타입 + fetch (docs/SUPPORT.md, GET /api/events/{id}/support). 서버 컴포넌트(근거 화면)에서 부른다.
 * 연결 기사에서 규칙으로 뽑은 지원 언급이며 사건 상태(5종)와 무관한 보조 근거다.
 */
import { getJson } from "./press";

export type SupportActorKind = "기업" | "단체" | "개인";
export type SupportActorRole = "당사자" | "제3자";

export interface SupportItem {
  news_id: string;
  title: string;
  published_at: string | null; // YYYY-MM-DD
  provider: string;
  provider_link_page: string | null;
  actor: string; // 개인은 이름 없이 '개인'
  actor_kind: SupportActorKind;
  actor_role: SupportActorRole;
  support_terms: string[];
  evidence_span: string; // ≤200자
}

export interface EventSupportOut {
  event_id: number;
  count: number;
  actors: number;
  items: SupportItem[];
  note: string;
  disclaimer: string;
}

/** GET /api/events/{id}/support */
export function getEventSupport(eventId: number): Promise<EventSupportOut> {
  return getJson<EventSupportOut>(`/api/events/${eventId}/support`);
}
