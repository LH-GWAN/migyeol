import Link from "next/link";
import StatusBadge from "@/components/StatusBadge";
import { formatDaysAgo, showsDeadlinePassed } from "@/lib/format";
import type { EventCard } from "@/lib/types";

/** 확인 필요 상위 5 — 기존 우선순위(/api/events?needs_check=true&sort=priority) 카드의 축약형. */
export default function NeedsCheckTop({ cards }: { cards: EventCard[] }) {
  if (cards.length === 0) return <p className="text-sm text-gray-500">지금 확인이 필요한 사건이 없습니다.</p>;
  return (
    <ol className="flex flex-col gap-2">
      {cards.map((c, i) => (
        <li key={c.id}>
          <Link
            href={`/events/${c.id}`}
            className="group flex items-start gap-3 rounded-lg border border-gray-200 bg-white p-3 transition hover:border-gray-300 hover:shadow-sm"
          >
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-900 text-xs font-bold text-white">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold leading-snug text-gray-900 group-hover:text-blue-700">{c.title}</span>
              <span className="mt-0.5 block text-xs text-gray-500">
                {c.responsible_org} · 마지막 후속 기사 {formatDaysAgo(c.days_since_last_followup)}
                {c.primary_promise ? ` · 발표: ${c.primary_promise.action}` : ""}
              </span>
              <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <StatusBadge status={c.status} size="sm" />
                {showsDeadlinePassed(c.status, c.deadline_passed_days) && (
                  <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-700">
                    기한 경과 {c.deadline_passed_days}일
                  </span>
                )}
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}
