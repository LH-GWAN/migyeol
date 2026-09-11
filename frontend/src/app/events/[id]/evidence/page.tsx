import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArticleLink, ArticleMeta } from "@/components/ArticleItem";
import Disclaimer from "@/components/Disclaimer";
import ErrorBox from "@/components/ErrorBox";
import ReportForm from "@/components/ReportForm";
import StatusBadge from "@/components/StatusBadge";
import { getEvidence, safe } from "@/lib/api";
import {
  formatDate,
  formatDateTime,
  formatPercent,
  formatScore,
  formatThreshold,
  SIGNAL_LABELS,
} from "@/lib/format";
import type { SignalKind } from "@/lib/types";

export const dynamic = "force-dynamic";

type Params = Promise<{ id: string }>;

const SIGNAL_STYLE: Record<SignalKind, string> = {
  완료: "border-green-300 bg-green-50 text-green-800",
  진행: "border-blue-300 bg-blue-50 text-blue-800",
  새로운문제: "border-orange-300 bg-orange-50 text-orange-800",
  무관: "border-gray-200 bg-gray-50 text-gray-500",
  불명확: "border-dashed border-gray-400 bg-white text-gray-600",
};

function SignalBadge({ signal }: { signal: string }) {
  const known = signal in SIGNAL_STYLE;
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${
        known ? SIGNAL_STYLE[signal as SignalKind] : "border-gray-200 bg-gray-50 text-gray-500"
      }`}
    >
      {known ? SIGNAL_LABELS[signal as SignalKind] : signal}
    </span>
  );
}

function ConfidenceBar({ value, threshold }: { value: number; threshold?: number }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  const ok = threshold === undefined || value >= threshold;
  return (
    <div className="flex min-w-[96px] flex-col gap-1">
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-gray-200" aria-hidden>
        <div className={`h-full rounded-full ${ok ? "bg-blue-500" : "bg-gray-400"}`} style={{ width: `${pct}%` }} />
        {threshold !== undefined && (
          <div className="absolute top-0 h-full w-px bg-gray-700" style={{ left: `${threshold * 100}%` }} />
        )}
      </div>
      <span className="text-xs text-gray-700">
        {formatPercent(value)}
        {threshold !== undefined && <span className="text-gray-400"> / 기준 {formatPercent(threshold)}</span>}
      </span>
    </div>
  );
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { id } = await params;
  return { title: `사건 #${id} 근거·불확실성` };
}

export default async function EvidencePage({ params }: { params: Params }) {
  const { id } = await params;
  const eventId = Number(id);
  if (!Number.isInteger(eventId) || eventId <= 0) notFound();

  const result = await safe(getEvidence(eventId));
  if (!result.ok) {
    if (result.error.isNotFound) notFound();
    return (
      <div className="flex flex-col gap-4">
        <Link href={`/events/${eventId}`} className="text-sm text-blue-700 hover:underline">
          ← 사건 타임라인으로
        </Link>
        <ErrorBox title={`사건 #${eventId}의 근거 정보를 불러오지 못했습니다`} error={result.error} />
      </div>
    );
  }

  const ev = result.data;
  const isUnresolved = ev.status === "판단 불가" || ev.status === "후속보도 부족";
  const signalMin = ev.rule.thresholds?.signal_min_conf;
  const thresholds = Object.entries(ev.rule.thresholds ?? {});
  const counted = ev.signals.filter((s) => s.counted).length;

  return (
    <div className="flex flex-col gap-6">
      <nav className="text-sm text-gray-500" aria-label="경로">
        <Link href="/" className="text-blue-700 hover:underline">
          목록
        </Link>
        <span className="mx-2">›</span>
        <Link href={`/events/${ev.event_id}`} className="text-blue-700 hover:underline">
          {ev.title}
        </Link>
        <span className="mx-2">›</span>
        <span className="text-gray-700">근거·불확실성</span>
      </nav>

      <header className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <p className="text-sm text-gray-500">근거·불확실성</p>
        <h1 className="mt-1 text-2xl font-bold leading-tight text-gray-900">{ev.title}</h1>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <StatusBadge status={ev.status} size="lg" />
          <span className="text-sm text-gray-600">판정 신뢰도 {formatPercent(ev.confidence)}</span>
          {ev.judged_at && <span className="text-xs text-gray-400">판정 시각 {formatDateTime(ev.judged_at)}</span>}
        </div>
        <Disclaimer text={ev.disclaimer} className="mt-3" />
      </header>

      {/* How the status was decided */}
      <section className="rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="text-base font-bold text-gray-900">이 상태는 어떻게 판정되었나</h2>
        <p className="mt-2 rounded-md bg-gray-50 px-4 py-3 text-[15px] font-medium text-gray-900">{ev.rule.text}</p>
        <p className="mt-2 text-xs text-gray-500">
          적용 규칙 <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] text-gray-700">{ev.rule.name}</code>
        </p>
        {thresholds.length > 0 && (
          <div className="mt-3">
            <h3 className="text-xs font-semibold text-gray-500">판정 기준값</h3>
            <dl className="mt-1 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
              {thresholds.map(([key, value]) => {
                const t = formatThreshold(key, value);
                return (
                  <div key={key} className="contents">
                    <dt className="text-gray-600">
                      {t.label} <span className="font-mono text-[11px] text-gray-400">({key})</span>
                    </dt>
                    <dd className="font-medium text-gray-900">{t.value}</dd>
                  </div>
                );
              })}
            </dl>
          </div>
        )}
      </section>

      {isUnresolved && (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-5">
          <h2 className="text-base font-bold text-amber-900">
            {ev.status === "판단 불가" ? "판단을 보류한 이유" : "후속보도가 부족한 이유"}
          </h2>
          <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[max-content_1fr]">
            <dt className="text-amber-800">사유</dt>
            <dd className="text-amber-950">{ev.unresolved_reason ?? "사유가 기록되지 않았습니다."}</dd>
            <dt className="text-amber-800">마지막 후속 기사일</dt>
            <dd className="text-amber-950">{ev.last_followup_at ? formatDate(ev.last_followup_at) : "후속 기사 없음"}</dd>
            <dt className="text-amber-800">다음 자동 재조회 예정</dt>
            <dd className="text-amber-950">{ev.next_scheduled_run ? formatDateTime(ev.next_scheduled_run) : "예정 없음"}</dd>
          </dl>
          <Disclaimer text={ev.disclaimer} className="mt-3 text-amber-800" />
        </section>
      )}

      {/* Signals */}
      <section className="rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="text-base font-bold text-gray-900">
          후속 신호 <span className="text-sm font-normal text-gray-500">유효 {counted}건 / 전체 {ev.signals.length}건</span>
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          약속과 일치하고 신뢰도가 기준 이상인 신호만 판정에 반영됩니다. 반영되지 않은 신호는 흐리게 표시하고 「판정 미반영」으로 표기합니다.
        </p>
        {ev.signals.length === 0 ? (
          <p className="mt-3 rounded-md border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
            분류된 후속 신호가 없습니다.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-xs text-gray-500">
                  <th className="py-2 pr-3 font-semibold">신호</th>
                  <th className="py-2 pr-3 font-semibold">발췌 문장</th>
                  <th className="py-2 pr-3 font-semibold">기사</th>
                  <th className="py-2 pr-3 font-semibold">신뢰도</th>
                  <th className="py-2 pr-3 font-semibold">모델 근거</th>
                  <th className="py-2 font-semibold">반영</th>
                </tr>
              </thead>
              <tbody>
                {ev.signals.map((s) => (
                  <tr
                    key={s.id}
                    className={`border-b border-gray-100 align-top ${s.counted ? "" : "opacity-60"}`}
                  >
                    <td className="py-3 pr-3">
                      <SignalBadge signal={s.signal} />
                      <p className="mt-1 text-[11px] text-gray-400">
                        {s.promise_id !== null ? `약속 #${s.promise_id} ` : ""}
                        {s.matches_promise ? "일치" : "불일치"}
                      </p>
                    </td>
                    <td className="py-3 pr-3 text-gray-800">
                      <q className="leading-relaxed">{s.evidence_span}</q>
                    </td>
                    <td className="py-3 pr-3">
                      <ArticleLink article={s.article} className="text-sm" />
                      <div className="mt-0.5">
                        <ArticleMeta article={s.article} />
                      </div>
                    </td>
                    <td className="py-3 pr-3">
                      <ConfidenceBar value={s.confidence} threshold={signalMin} />
                    </td>
                    <td className="py-3 pr-3 text-xs leading-relaxed text-gray-600">{s.rationale}</td>
                    <td className="py-3">
                      {s.counted ? (
                        <span className="whitespace-nowrap text-xs font-semibold text-green-700">반영</span>
                      ) : (
                        <span className="whitespace-nowrap rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">
                          판정 미반영
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Low-confidence links */}
      <section className="rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="text-base font-bold text-gray-900">
          저신뢰 연결 <span className="text-sm font-normal text-gray-500">{ev.low_confidence_links.length}건</span>
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          이 사건과 같은 사건인지 확실하지 않은 기사입니다. 점수와 통과한 규칙을 함께 표시하며, 잘못된 연결은 신고할 수 있습니다.
        </p>
        {ev.low_confidence_links.length === 0 ? (
          <p className="mt-3 rounded-md border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
            저신뢰 연결이 없습니다.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-gray-100">
            {ev.low_confidence_links.map((l) => (
              <li key={l.article.news_id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <ArticleLink article={l.article} className="text-sm" />
                    <ArticleMeta article={l.article} />
                  </div>
                  {l.article.hilight && <p className="mt-1 text-xs text-gray-600">{l.article.hilight}</p>}
                  <p className="mt-1 text-xs text-gray-500">
                    연결 점수 <span className="font-semibold text-gray-800">{formatScore(l.link_score)}</span> · 통과한 규칙:{" "}
                    <span className="font-mono">{l.link_reason || "-"}</span>
                  </p>
                </div>
                <ReportForm
                  eventId={ev.event_id}
                  type="wrong_link"
                  newsId={l.article.news_id}
                  label="잘못된 연결 신고"
                  description="이 기사가 이 사건과 관계없다고 판단되면 신고해 주세요."
                  size="sm"
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Dispute status */}
      <section className="rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="text-base font-bold text-gray-900">상태 판정에 이의가 있나요?</h2>
        <p className="mt-1 text-xs text-gray-500">
          판정에 반영되지 않은 보도나 근거가 있다면 알려주세요. 검토 큐에 등록되어 담당자가 확인합니다.
        </p>
        <div className="mt-3">
          <ReportForm
            eventId={ev.event_id}
            type="wrong_status"
            label="상태 이의 제기"
            description="현재 상태 판정이 잘못되었다고 보는 이유를 적어 주세요."
          />
        </div>
      </section>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link href={`/events/${ev.event_id}`} className="text-sm text-blue-700 hover:underline">
          ← 사건 타임라인으로
        </Link>
        <Link href="/" className="text-sm text-blue-700 hover:underline">
          목록으로
        </Link>
      </div>
    </div>
  );
}
