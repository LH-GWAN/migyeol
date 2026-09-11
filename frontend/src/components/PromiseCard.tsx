import { ArticleLink, ArticleMeta } from "@/components/ArticleItem";
import { formatDate, formatPercent, STRENGTH_LABELS } from "@/lib/format";
import type { PromiseOut, Strength } from "@/lib/types";

const STRENGTH_STYLE: Record<Strength, string> = {
  확약: "border-indigo-300 bg-indigo-50 text-indigo-800",
  계획: "border-sky-300 bg-sky-50 text-sky-800",
  의사표명: "border-gray-300 bg-gray-50 text-gray-700",
  해당없음: "border-gray-200 bg-white text-gray-400",
};

interface Props {
  promise: PromiseOut;
}

/** Extracted promise (주체·행동·기한·발언 강도·원문 인용문·근거 기사). */
export default function PromiseCard({ promise }: Props) {
  const deadline =
    promise.deadline_date && promise.deadline_precision !== "none"
      ? formatDate(promise.deadline_date, promise.deadline_precision)
      : null;

  return (
    <article className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${STRENGTH_STYLE[promise.strength] ?? STRENGTH_STYLE.해당없음}`}
        >
          {STRENGTH_LABELS[promise.strength] ?? promise.strength}
        </span>
        <span className="text-xs text-gray-500">
          {promise.is_trackable ? "추적 가능" : "추적 불가(기한·행동 불명확)"} · 추출 신뢰도 {formatPercent(promise.confidence)}
        </span>
        {promise.deadline_passed && (
          <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">
            기한 경과
          </span>
        )}
      </div>

      <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
        <dt className="text-gray-500">주체</dt>
        <dd className="text-gray-900">
          <span className="font-semibold">{promise.actor_org}</span>
          {promise.actor_raw && promise.actor_raw !== promise.actor_org && (
            <span className="ml-1 text-gray-500">({promise.actor_raw})</span>
          )}
        </dd>
        <dt className="text-gray-500">행동</dt>
        <dd className="text-gray-900">{promise.action}</dd>
        <dt className="text-gray-500">기한</dt>
        <dd className="text-gray-900">
          {deadline ?? <span className="text-gray-400">기한 없음</span>}
          {promise.deadline_raw && <span className="ml-1 text-gray-500">(원문: {promise.deadline_raw})</span>}
        </dd>
      </dl>

      {promise.quotation && (
        <blockquote className="mt-3 border-l-2 border-indigo-300 pl-3 text-sm text-gray-800">
          <p>“{promise.quotation.quotation}”</p>
          <footer className="mt-1 text-xs text-gray-500">
            — {promise.quotation.source} · {promise.quotation.provider} · {formatDate(promise.quotation.published_at)}
          </footer>
        </blockquote>
      )}

      {promise.evidence_article && (
        <p className="mt-3 flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="text-xs text-gray-500">근거 기사</span>
          <ArticleLink article={promise.evidence_article} className="text-sm" />
          <ArticleMeta article={promise.evidence_article} />
        </p>
      )}
    </article>
  );
}
