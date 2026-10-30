import Link from "next/link";
import StatusBadge from "@/components/StatusBadge";
import { cleanSnippet, formatDate } from "@/lib/format";
import type { NoticeCase, NoticeCaseKind } from "@/lib/dashboard";

/**
 * 기관 발표로 확인된 사례 (2026-10-27 멘토 피드백). 두 종류 — ① 기관 발표 뒤 확인된 후속보도가 없는 사건,
 * ② 기관 발표로부터 30일 이상 지나서야 다음 후속보도가 확인된 사건. 숫자·날짜·제목은 전부 /api/dashboard/notice-cases 에서 온다.
 * 기관 게시판 항목은 보조 근거이며 사건 상태(5종)를 바꾸지 않는다.
 */

const KIND_STYLE: Record<NoticeCaseKind, { chip: string; bar: string }> = {
  notice_without_followup: { chip: "border-violet-300 bg-violet-50 text-violet-800", bar: "bg-violet-500" },
  notice_before_followup: { chip: "border-sky-300 bg-sky-50 text-sky-800", bar: "bg-sky-500" },
};

function Step({ label, date, muted = false }: { label: string; date: string | null; muted?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-gray-500">{label}</dt>
      <dd className={`text-sm font-semibold tabular-nums ${muted ? "text-gray-400" : "text-gray-900"}`}>{date ? formatDate(date) : "없음"}</dd>
    </div>
  );
}

function gapText(c: NoticeCase): string {
  if (c.gap_days === null) return "";
  if (c.kind === "notice_without_followup") {
    const when = c.gap_days === 0 ? "발생 당일" : `발생 ${c.gap_days}일 뒤`;
    return `${when} 기관 발표 · 그 뒤 확인된 후속보도 없음 (기관 발언 이후 연결 기사 ${c.articles_after}건)`;
  }
  return `기관 발표 ${c.gap_days}일 뒤에야 다음 후속보도 확인`;
}

export default function NoticeCases({ items }: { items: NoticeCase[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-gray-500">기관 공식 게시판에서 발표가 확인된 사례가 아직 없습니다.</p>;
  }
  return (
    <ul className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {items.map((c) => {
        const style = KIND_STYLE[c.kind];
        return (
          <li key={`${c.kind}-${c.event_id}`} className="relative flex flex-col gap-3 overflow-hidden rounded-xl border border-gray-200 bg-white p-4">
            <span aria-hidden className={`absolute inset-y-0 left-0 w-1 ${style.bar}`} />
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${style.chip}`}>{c.kind_label}</span>
              <StatusBadge status={c.status} size="sm" />
            </div>
            <div>
              <Link href={`/events/${c.event_id}`} className="text-base font-bold leading-snug text-gray-900 hover:text-blue-700 hover:underline">
                {c.title}
              </Link>
              <p className="mt-0.5 text-xs text-gray-500">책임기관 {c.org}</p>
            </div>

            <dl className="grid grid-cols-3 gap-2 rounded-lg bg-gray-50 px-3 py-2">
              <Step label="사건 발생" date={c.occurred_at} />
              <Step label="기관 발표" date={c.notice.published_at} />
              {c.kind === "notice_without_followup" ? (
                <Step label="후속보도 확인" date={null} muted />
              ) : (
                <Step label="다음 후속보도 확인" date={c.followup_after_at ?? c.latest_confirmed_at} />
              )}
            </dl>
            {gapText(c) && <p className="text-xs font-semibold text-gray-700">{gapText(c)}</p>}

            <div className="rounded-lg border border-gray-100 bg-white p-3">
              <p className="text-[11px] text-gray-500">
                {c.notice.site_name}
                {c.notice.published_at ? ` · ${formatDate(c.notice.published_at)}` : ""}
                {c.notices_count > 1 ? ` · 관련 발표 ${c.notices_count}건 중 1건` : ""}
              </p>
              <a href={c.notice.url} target="_blank" rel="noopener noreferrer" className="mt-0.5 block text-sm font-semibold text-blue-700 hover:underline">
                {c.notice.title}
              </a>
              {cleanSnippet(c.notice.snippet) && <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-gray-600">{cleanSnippet(c.notice.snippet)}</p>}
            </div>

            <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
              <a href={c.notice.site_url} target="_blank" rel="noopener noreferrer" className="font-semibold text-gray-700 hover:text-blue-700 hover:underline">
                기관 게시판으로 ↗
              </a>
              <Link href={`/events/${c.event_id}/evidence#org-notice-heading`} className="font-semibold text-blue-700 hover:underline">
                사건 근거 보기 →
              </Link>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
