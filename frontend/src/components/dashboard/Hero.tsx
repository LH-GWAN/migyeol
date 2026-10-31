import Link from "next/link";
import { formatPercent } from "@/lib/format";
import type { DashboardSummary } from "@/lib/dashboard";

/**
 * 첫 화면 머리: "추적 중인 사건 N건 중 M건에서 후속보도가 확인됐습니다" + 비율 링 하나(화면의 유일한 큰 숫자).
 * 실험 리포트가 있으면 "기관 대응 발언 기사 X건(인용문 검색 수집) → 사건 Y건 → 후속보도 확인 Z건" 흐름을 한 줄로 덧붙인다.
 */
export default function Hero({ s }: { s: DashboardSummary }) {
  const rate = Math.max(0, Math.min(1, s.followup_rate));
  const R = 52;
  const C = 2 * Math.PI * R;
  const exp = s.experiment;

  return (
    <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-950 via-slate-900 to-indigo-950 px-5 py-7 text-white shadow-lg sm:px-8 sm:py-9">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-indigo-500/20 blur-3xl"
      />
      <div aria-hidden className="pointer-events-none absolute -bottom-28 left-1/3 h-64 w-64 rounded-full bg-sky-500/10 blur-3xl" />

      <div className="relative flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0 md:max-w-[62%]">
          <p className="text-xs font-semibold tracking-wide text-sky-300">재난·안전 사건 후속보도 현황 · 기준일 {s.as_of}</p>
          <h1 className="mt-2 text-2xl font-bold leading-snug sm:text-3xl">
            기관이 약속한 조치,
            <br />
            그 뒤에도 보도됐을까요?
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-slate-300 sm:text-base">
            추적 중인 사건 <b className="text-white">{s.events_total}건</b> 중{" "}
            <b className="text-white">{s.with_followup}건</b>은 조치 완료·진행이나 새로운 문제를 전한 후속보도가 확인됐습니다. 나머지{" "}
            <b className="text-white">{s.events_total - s.with_followup}건</b>은 그런 보도가 아직 기준에 맞게 확인되지 않았습니다(신호는 있지만 기준에 못 미친 사건 포함).
          </p>
          {exp && (
            <p className="mt-3 inline-flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-white/5 px-3 py-2 text-xs text-slate-300 ring-1 ring-white/10">
              <span>
                기관 대응 발언 기사 <b className="text-white">{exp.source_articles.collected.toLocaleString("ko-KR")}건</b>
              </span>
              <span aria-hidden>→</span>
              <span>
                사건 <b className="text-white">{exp.events.total}건</b>
              </span>
              <span aria-hidden>→</span>
              <span>
                후속보도 확인 <b className="text-white">{exp.events.with_followup}건</b>
              </span>
            </p>
          )}
          <div className="mt-5 flex flex-wrap gap-2 text-sm">
            <Link href="/events" className="rounded-lg bg-white px-3.5 py-2 font-semibold text-slate-900 hover:bg-slate-100">
              확인이 필요한 사건 보기
            </Link>
            <Link href="/events?tab=followup" className="rounded-lg px-3.5 py-2 font-semibold text-white ring-1 ring-white/30 hover:bg-white/10">
              후속보도가 확인된 사건
            </Link>
          </div>
        </div>

        <figure className="flex items-center gap-5 self-start md:self-center">
          <svg viewBox="0 0 128 128" className="h-32 w-32 shrink-0 sm:h-36 sm:w-36" role="img" aria-label={`후속보도 확인 비율 ${formatPercent(rate, 1)}`}>
            <circle cx="64" cy="64" r={R} fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="12" />
            <circle
              cx="64"
              cy="64"
              r={R}
              fill="none"
              stroke="#5598e7"
              strokeWidth="12"
              strokeLinecap="round"
              strokeDasharray={`${C * rate} ${C}`}
              transform="rotate(-90 64 64)"
            />
            <text x="64" y="62" textAnchor="middle" fontSize="26" fontWeight="700" fill="#fff">
              {formatPercent(rate)}
            </text>
            <text x="64" y="82" textAnchor="middle" fontSize="10" fill="#cbd5e1">
              후속보도 확인
            </text>
          </svg>
          <figcaption className="text-sm leading-relaxed text-slate-300">
            <span className="block text-2xl font-bold tabular-nums text-white">
              {s.with_followup}
              <span className="ml-1 text-base font-medium text-slate-400">/ {s.events_total}건</span>
            </span>
            기관 발언 이후 연결 기사 {s.followup_articles}건 · 기관 {s.orgs}곳
          </figcaption>
        </figure>
      </div>

      <p className="relative mt-6 border-t border-white/10 pt-3 text-xs text-slate-400">※ {s.disclaimer}</p>
    </section>
  );
}
