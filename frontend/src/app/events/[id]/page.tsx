import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import ArticleItem from "@/components/ArticleItem";
import Disclaimer from "@/components/Disclaimer";
import ErrorBox from "@/components/ErrorBox";
import PromiseCard from "@/components/PromiseCard";
import StatusBadge from "@/components/StatusBadge";
import Stepper, { STEP_PHASES } from "@/components/Stepper";
import TrendChart from "@/components/TrendChart";
import { getEvent, safe } from "@/lib/api";
import { formatDate, formatDateTime, formatDaysAgo, formatPercent, PHASE_LABELS } from "@/lib/format";
import type { ArticleOut, Phase } from "@/lib/types";

export const dynamic = "force-dynamic";

type Params = Promise<{ id: string }>;

function parseId(raw: string): number | null {
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { id } = await params;
  return { title: `사건 #${id} 타임라인` };
}

export default async function EventPage({ params }: { params: Params }) {
  const { id } = await params;
  const eventId = parseId(id);
  if (eventId === null) notFound();

  const result = await safe(getEvent(eventId));
  if (!result.ok) {
    if (result.error.isNotFound) notFound();
    return (
      <div className="flex flex-col gap-4">
        <Link href="/" className="text-sm text-blue-700 hover:underline">
          ← 목록으로
        </Link>
        <ErrorBox title={`사건 #${eventId} 정보를 불러오지 못했습니다`} error={result.error} />
      </div>
    );
  }

  const { card, summary, seed_query, status_reason, status_reason_text, latest_judgment, promises, timeline, trend, disclaimer } =
    result.data;

  const byPhase = new Map<Phase, ArticleOut[]>();
  for (const t of timeline) byPhase.set(t.phase, t.articles);
  const others = byPhase.get("기타") ?? [];

  return (
    <div className="flex flex-col gap-6">
      <nav className="text-sm text-gray-500" aria-label="경로">
        <Link href="/" className="text-blue-700 hover:underline">
          목록
        </Link>
        <span className="mx-2">›</span>
        <span className="text-gray-700">{card.title}</span>
      </nav>

      {/* Header summary */}
      <header className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-bold leading-tight text-gray-900">{card.title}</h1>
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
            <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr_max-content_1fr]">
              <dt className="text-gray-500">발생일</dt>
              <dd className="text-gray-900">{formatDate(card.occurred_at)}</dd>
              <dt className="text-gray-500">지역</dt>
              <dd className="text-gray-900">
                {card.region}
                {card.facility ? ` · ${card.facility}` : ""}
              </dd>
              <dt className="text-gray-500">책임 기관</dt>
              <dd className="text-gray-900">{card.responsible_org}</dd>
              <dt className="text-gray-500">사건 유형</dt>
              <dd className="text-gray-900">{card.incident_type}</dd>
              <dt className="text-gray-500">최근 후속 기사</dt>
              <dd className="text-gray-900">
                {formatDaysAgo(card.days_since_last_followup)}
                {card.last_followup_at ? ` (${formatDate(card.last_followup_at)})` : ""}
              </dd>
              <dt className="text-gray-500">기사 수</dt>
              <dd className="text-gray-900">
                전체 {card.article_count}건 · 후속 {card.followup_count}건 · 근거 {card.evidence_count}건
              </dd>
            </dl>
          </div>

          <div className="flex w-full flex-col gap-2 rounded-md border border-gray-100 bg-gray-50 p-4 sm:w-auto sm:min-w-[260px]">
            <span className="text-xs font-semibold text-gray-500">현재 상태</span>
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={card.status} size="lg" />
              <span className="text-sm text-gray-600">신뢰도 {formatPercent(card.status_confidence)}</span>
            </div>
            <p className="text-xs text-gray-600">
              {status_reason_text}
              <span className="ml-1 font-mono text-[11px] text-gray-400">({status_reason})</span>
            </p>
            {latest_judgment?.unresolved_reason && (
              <p className="text-xs text-amber-800">사유: {latest_judgment.unresolved_reason}</p>
            )}
            {latest_judgment && (
              <p className="text-[11px] text-gray-400">판정 {formatDateTime(latest_judgment.judged_at)}</p>
            )}
            <Link
              href={`/events/${card.id}/evidence`}
              className="mt-1 inline-flex items-center justify-center rounded-md bg-gray-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-gray-700"
            >
              근거·불확실성 보기 →
            </Link>
          </div>
        </div>
        <Disclaimer text={disclaimer} className="mt-4 border-t border-gray-100 pt-3" />
      </header>

      {summary && (
        <section className="rounded-lg border border-gray-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-gray-500">사건 요약</h2>
          <p className="mt-1 text-sm leading-relaxed text-gray-800">{summary}</p>
        </section>
      )}

      {/* Stepper */}
      <section className="rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="mb-5 text-base font-bold text-gray-900">진행 단계</h2>
        <Stepper timeline={timeline} />
        <p className="mt-4 text-xs text-gray-500">
          단계는 수집된 기사의 분류 결과입니다. 비어 있는 단계는 해당 보도를 찾지 못했다는 뜻이며, 조치가 없었다는 뜻이 아닙니다.
        </p>
      </section>

      {/* Trend */}
      <section className="rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="text-base font-bold text-gray-900">보도량 추이</h2>
        <p className="mb-3 text-xs text-gray-500">월별 관련 기사 수. 세로선은 발생일·약속 기한·오늘을 나타냅니다.</p>
        <TrendChart trend={trend} />
      </section>

      {/* Per-phase article lists */}
      <div className="flex flex-col gap-4">
        {STEP_PHASES.map((phase, i) => {
          const articles = byPhase.get(phase) ?? [];
          const isResponse = phase === "대응발표";
          return (
            <section
              key={phase}
              id={`phase-${phase}`}
              className="scroll-mt-4 rounded-lg border border-gray-200 bg-white p-5"
            >
              <h2 className="flex items-baseline gap-2 text-base font-bold text-gray-900">
                <span className="text-sm font-semibold text-gray-400">{i + 1}</span>
                {PHASE_LABELS[phase]}
                <span className="text-sm font-normal text-gray-500">
                  {articles.length > 0 ? `기사 ${articles.length}건` : "기사 없음"}
                </span>
              </h2>

              {isResponse && (
                <div className="mt-3">
                  <h3 className="text-sm font-semibold text-gray-700">추출된 약속 {promises.length}건</h3>
                  {promises.length === 0 ? (
                    <p className="mt-1 text-sm text-gray-400">이 사건에서 추출된 약속이 없습니다.</p>
                  ) : (
                    <ul className="mt-2 flex flex-col gap-3">
                      {promises.map((p) => (
                        <li key={p.id}>
                          <PromiseCard promise={p} />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {articles.length === 0 ? (
                <p className="mt-3 rounded-md border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
                  이 단계의 보도를 찾지 못함
                </p>
              ) : (
                <ul className={`${isResponse ? "mt-4" : "mt-3"} divide-y divide-gray-100`}>
                  {articles.map((a) => (
                    <ArticleItem key={a.news_id} article={a} />
                  ))}
                </ul>
              )}
            </section>
          );
        })}

        <details id="phase-기타" className="rounded-lg border border-gray-200 bg-white p-5">
          <summary className="cursor-pointer text-base font-bold text-gray-700">
            {PHASE_LABELS["기타"]}{" "}
            <span className="text-sm font-normal text-gray-500">
              {others.length > 0 ? `기사 ${others.length}건` : "기사 없음"}
            </span>
          </summary>
          {others.length === 0 ? (
            <p className="mt-3 text-sm text-gray-400">단계로 분류되지 않은 관련 기사가 없습니다.</p>
          ) : (
            <ul className="mt-3 divide-y divide-gray-100">
              {others.map((a) => (
                <ArticleItem key={a.news_id} article={a} />
              ))}
            </ul>
          )}
        </details>
      </div>

      <details className="rounded-lg border border-gray-200 bg-white p-5 text-sm">
        <summary className="cursor-pointer font-semibold text-gray-600">수집 조건 보기</summary>
        <p className="mt-2 text-xs text-gray-500">빅카인즈 검색식</p>
        <code className="mt-1 block whitespace-pre-wrap rounded bg-gray-50 p-2 font-mono text-xs text-gray-800">{seed_query}</code>
      </details>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link href="/" className="text-sm text-blue-700 hover:underline">
          ← 목록으로
        </Link>
        <Link href={`/events/${card.id}/evidence`} className="text-sm font-semibold text-blue-700 hover:underline">
          근거·불확실성 보기 →
        </Link>
      </div>
    </div>
  );
}
