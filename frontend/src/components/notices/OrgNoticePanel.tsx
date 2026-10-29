import Link from "next/link";
import { ShowMoreList } from "@/components/ShowMore";
import type { Result } from "@/lib/api";
import { cleanSnippet, formatDate, formatDateTime } from "@/lib/format";
import type { EventNoticesOut, NoticeState } from "@/lib/notices";

/**
 * 근거 화면의 "기관 발표 확인" 패널 (v2 §6 화면 3·§3). 항목 목록, 또는
 * 트리거 조건 미충족 / 등록된 게시판 없음 / 확인했으나 없음 / 아직 확인 전 중 하나를 명시한다.
 * 기관 게시판 항목은 보조 근거이며 사건 상태(5종)를 바꾸지 않는다.
 */

const STATE_STYLE: Record<NoticeState, string> = {
  found: "border-emerald-300 bg-emerald-50 text-emerald-800",
  not_triggered: "border-gray-200 bg-gray-50 text-gray-600",
  no_site: "border-dashed border-gray-400 bg-white text-gray-700",
  not_found: "border-gray-300 bg-gray-100 text-gray-700",
  not_checked: "border-amber-300 bg-amber-50 text-amber-800",
  error: "border-red-200 bg-red-50 text-red-800",
};

function explain(n: EventNoticesOut): string {
  switch (n.state) {
    case "not_triggered":
      return `${n.reason}. 상태가 '후속보도 부족'·'판단 불가'이고 마지막 보도·약속 이후 ${n.trigger_days}일 이상 지난 사건만 책임기관 게시판을 확인합니다 — 이 사건에는 어떤 요청도 보내지 않았습니다.`;
    case "no_site":
      return `${n.reason}. '${n.org ?? "기관 미상"}'의 공식 보도자료·공지사항 게시판이 등록부에 없어 확인하지 않았습니다.`;
    case "not_found":
      return `${n.checked_at ? `${formatDateTime(n.checked_at)}에 ` : ""}책임기관 게시판 목록을 확인했으나, 사건 시설명·핵심어·약속 행동과 맞는 제목이 확인되지 않았습니다.`;
    case "not_checked":
      return `${n.reason}. 확인 조건은 충족했고, 다음 확인 실행 때 책임기관 게시판을 봅니다.`;
    case "error":
      return "게시판에 접속하지 못했습니다(사이트 응답 오류 또는 robots.txt 제한). 다음 실행에서 다시 확인합니다.";
    default:
      return "";
  }
}

export default function OrgNoticePanel({ eventId, result }: { eventId: number; result: Result<EventNoticesOut> }) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5" aria-labelledby="org-notice-heading">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="org-notice-heading" className="text-base font-bold text-gray-900">
            기관 발표 확인
          </h2>
          <p className="mt-1 text-xs text-gray-500">
            후속보도가 오래 없는 사건은 책임기관의 공식 보도자료·공지사항 게시판(목록 제목)을 확인합니다. 여기서 찾은 항목은 보조
            근거이며 사건 상태를 바꾸지 않습니다.
          </p>
        </div>
        {result.ok && (
          <span className={`whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold ${STATE_STYLE[result.data.state] ?? STATE_STYLE.not_found}`}>
            {result.data.state_label}
          </span>
        )}
      </div>

      {!result.ok ? (
        <p className="mt-3 rounded-md border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
          기관 발표 확인 결과를 불러오지 못했습니다. ({result.error.message})
        </p>
      ) : result.data.items.length > 0 ? (
        <>
          <p className="mt-3 text-sm text-emerald-900">
            뉴스에는 확인되지 않았지만 책임기관 게시판에서 관련 발표 {result.data.items.length}건이 확인됐습니다.
          </p>
          <div className="mt-2">
            <ShowMoreList
              initial={3}
              moreLabel="발표 더보기"
              items={result.data.items.map((it) => (
              <li key={it.url} className="py-3">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <a href={it.url} target="_blank" rel="noopener noreferrer" className="text-sm font-semibold text-blue-700 hover:underline">
                    {it.title}
                  </a>
                  <span className="text-xs text-gray-500">
                    {it.published_at ? formatDate(it.published_at) : "게시일 미상"} · {it.site_name}
                  </span>
                </div>
                {cleanSnippet(it.snippet) && <p className="mt-1 text-xs leading-relaxed text-gray-600">{cleanSnippet(it.snippet)}</p>}
                <p className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-gray-500">
                  매칭 점수 <span className="font-semibold text-gray-800">{it.match_score.toFixed(2)}</span>
                  {it.matched_terms.map((t) => (
                    <span key={t} className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">
                      {t}
                    </span>
                  ))}
                </p>
              </li>
            ))}
            />
          </div>
        </>
      ) : (
        <p className="mt-3 rounded-md bg-gray-50 px-4 py-3 text-sm text-gray-700">
          <span className="font-semibold text-gray-900">{result.data.state_label}</span> — {explain(result.data)}
        </p>
      )}

      {result.ok && (
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
          <dt className="text-gray-500">책임기관</dt>
          <dd className="text-gray-800">{result.data.org ?? "기관 미상"}</dd>
          <dt className="text-gray-500">등록된 게시판</dt>
          <dd className="text-gray-800">
            {result.data.sites.length === 0
              ? "없음"
              : result.data.sites.map((s, i) => (
                  <span key={s.url}>
                    {i > 0 && " · "}
                    <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-blue-700 hover:underline">
                      {s.name}
                    </a>
                  </span>
                ))}
          </dd>
          <dt className="text-gray-500">마지막 확인</dt>
          <dd className="text-gray-800">{result.data.checked_at ? formatDateTime(result.data.checked_at) : "확인 기록 없음"}</dd>
        </dl>
      )}
      <p className="mt-3 text-xs">
        <Link href={`/press?event_id=${eventId}`} className="text-blue-700 hover:underline">
          이 사건의 보도자료 초안 보기 →
        </Link>
      </p>
    </section>
  );
}
