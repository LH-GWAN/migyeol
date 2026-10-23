import Link from "next/link";
import { formatDate } from "@/lib/format";
import { SIGNAL_LABEL, type RecentFollowup } from "@/lib/dashboard";

/** 최근 후속보도 피드 — 최신 유효 신호 순. 기사 제목은 언론사 페이지(새 창)로, 사건명은 타임라인으로. */
const CHIP: Record<RecentFollowup["signal"], string> = {
  완료: "border-green-300 bg-green-50 text-green-800",
  진행: "border-blue-300 bg-blue-50 text-blue-800",
  새로운문제: "border-orange-300 bg-orange-50 text-orange-800",
};

export default function RecentFeed({ items }: { items: RecentFollowup[] }) {
  if (items.length === 0) return <p className="text-sm text-gray-500">아직 확인된 후속보도가 없습니다.</p>;
  return (
    <ol className="relative flex flex-col gap-4 border-l border-gray-200 pl-5">
      {items.map((f) => (
        <li key={`${f.event_id}-${f.news_id}`} className="relative">
          <span className="absolute -left-[25px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-gray-400 ring-1 ring-gray-200" aria-hidden />
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
            <time dateTime={f.published_at ?? undefined} className="font-semibold text-gray-700">
              {formatDate(f.published_at)}
            </time>
            <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${CHIP[f.signal]}`}>{SIGNAL_LABEL[f.signal]}</span>
            <span>{f.org}</span>
          </div>
          <Link href={`/events/${f.event_id}`} className="mt-1 block text-sm font-semibold text-gray-900 hover:text-blue-700 hover:underline">
            {f.event_title}
          </Link>
          {f.evidence_span && <p className="mt-1 text-sm leading-relaxed text-gray-700">“{f.evidence_span}”</p>}
          <p className="mt-1 text-xs text-gray-500">
            {f.link ? (
              <a href={f.link} target="_blank" rel="noopener noreferrer" className="text-blue-700 hover:underline">
                {f.title}
              </a>
            ) : (
              f.title
            )}{" "}
            · {f.provider}
          </p>
        </li>
      ))}
    </ol>
  );
}
