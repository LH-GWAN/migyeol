import Link from "next/link";
import Disclaimer from "@/components/Disclaimer";
import Sparkline from "@/components/Sparkline";
import StatusBadge from "@/components/StatusBadge";
import { formatDate, formatDaysAgo, formatPercent } from "@/lib/format";
import type { EventCard } from "@/lib/types";

interface Props {
  card: EventCard;
}

/**
 * Event card, laid out exactly as in the spec:
 *   가온시 달빛지하차도 침수 사고
 *   대응 주체 / 발표된 조치     가온시 / 배수펌프 용량 증설
 *   발표된 기한 / 최근 후속 기사   2026년 10월 / 32일 전
 *   현재 상태                   [후속보도 부족]   근거 기사 6건 보기
 */
export default function EventCardView({ card }: Props) {
  const promise = card.primary_promise;
  const deadline =
    promise && promise.deadline_date && promise.deadline_precision !== "none"
      ? formatDate(promise.deadline_date, promise.deadline_precision)
      : card.deadline_date
        ? formatDate(card.deadline_date, "month")
        : "기한 없음";

  return (
    <article className="relative rounded-lg border border-gray-200 bg-white p-5 shadow-sm transition hover:border-gray-300 hover:shadow">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-bold leading-snug text-gray-900">
            {/* Stretched link: the whole card navigates to the event detail page. */}
            <Link href={`/events/${card.id}`} className="after:absolute after:inset-0 after:content-['']">
              {card.title}
            </Link>
          </h2>
          <p className="mt-1 text-xs text-gray-500">
            발생 {formatDate(card.occurred_at)} · {card.region}
            {card.facility ? ` · ${card.facility}` : ""} · {card.incident_type} · 기사 {card.article_count}건
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {card.deadline_passed_days !== null && (
              <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">
                기한 경과 {card.deadline_passed_days}일
              </span>
            )}
            {card.needs_review && (
              <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-xs font-semibold text-violet-700">
                검토 필요
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Sparkline values={card.trend_sparkline} />
          <span className="text-[11px] text-gray-400">발생 이후 월별 보도량</span>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-1 gap-y-2 text-sm sm:grid-cols-[minmax(0,220px)_1fr] sm:gap-x-6">
        <dt className="text-gray-500">대응 주체 / 발표된 조치</dt>
        <dd className="text-gray-900">
          {promise ? (
            <>
              <span className="font-semibold">{promise.actor_org}</span> / {promise.action}
            </>
          ) : (
            <>
              <span className="font-semibold">{card.responsible_org}</span> /{" "}
              <span className="text-gray-400">추적 가능한 발표 조치 없음</span>
            </>
          )}
        </dd>

        <dt className="text-gray-500">발표된 기한 / 최근 후속 기사</dt>
        <dd className="text-gray-900">
          {deadline} / {formatDaysAgo(card.days_since_last_followup)}
          {card.last_followup_at && (
            <span className="ml-1 text-xs text-gray-400">({formatDate(card.last_followup_at)})</span>
          )}
        </dd>

        <dt className="text-gray-500">현재 상태</dt>
        <dd className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <StatusBadge status={card.status} />
          <span className="text-xs text-gray-500">신뢰도 {formatPercent(card.status_confidence)}</span>
          <Link
            href={`/events/${card.id}/evidence`}
            className="relative z-10 text-sm font-medium text-blue-700 underline-offset-2 hover:underline"
          >
            근거 기사 {card.evidence_count}건 보기
          </Link>
        </dd>
      </dl>

      <div className="mt-3 flex flex-wrap items-end justify-between gap-2 border-t border-gray-100 pt-3">
        <Disclaimer text={card.disclaimer} className="max-w-[720px]" />
        <span className="text-[11px] text-gray-400">우선순위 {card.priority_score.toFixed(1)}</span>
      </div>
    </article>
  );
}
