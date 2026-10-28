import type { Result } from "@/lib/api";
import { cleanSnippet, formatDate } from "@/lib/format";
import type { EventSupportOut, SupportActorKind, SupportItem } from "@/lib/support";

/**
 * 근거 화면의 「민간 지원 소식」 패널 (docs/SUPPORT.md). 「기관 발표 확인」 패널 바로 뒤에 둔다 —
 * 사건 발생 → 기관 발표 → 민간 지원 소식 → 후속보도 확인 순서.
 * 연결 기사의 빅카인즈 메타데이터에서 규칙으로 뽑은 지원 언급이며, 사건 상태(5종)를 바꾸지 않는 보조 근거다.
 * 별도 기업 칸·평가 표현은 두지 않고 날짜·주체·어휘·근거 문구·기사 링크만 사실대로 보인다.
 */

const KIND_STYLE: Record<SupportActorKind, string> = {
  기업: "border-sky-200 bg-sky-50 text-sky-800",
  단체: "border-teal-200 bg-teal-50 text-teal-800",
  개인: "border-gray-200 bg-gray-50 text-gray-700",
};

const SHOW_FIRST = 8;

function SupportRow({ item }: { item: SupportItem }) {
  const evidence = cleanSnippet(item.evidence_span);
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-xs text-gray-500">{item.published_at ? formatDate(item.published_at) : "게시일 미상"}</span>
        <span className="text-sm font-semibold text-gray-900">{item.actor}</span>
        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${KIND_STYLE[item.actor_kind] ?? KIND_STYLE.단체}`}>
          {item.actor_kind}
        </span>
        {item.actor_role === "당사자" && (
          <span className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">사건 당사자</span>
        )}
        {item.support_terms.map((t) => (
          <span key={t} className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-700">
            {t}
          </span>
        ))}
      </div>
      {evidence && (
        <p className="mt-1 text-xs leading-relaxed text-gray-600">
          <q>{evidence}</q>
        </p>
      )}
      <p className="mt-1 text-xs text-gray-500">
        {item.provider_link_page ? (
          <a href={item.provider_link_page} target="_blank" rel="noopener noreferrer" className="text-blue-700 hover:underline">
            {item.title}
          </a>
        ) : (
          <span className="text-gray-700">{item.title}</span>
        )}
        {item.provider ? ` · ${item.provider}` : ""}
      </p>
    </li>
  );
}

export default function SupportPanel({ result }: { result: Result<EventSupportOut> }) {
  const items = result.ok ? result.data.items : [];
  const head = items.slice(0, SHOW_FIRST);
  const rest = items.slice(SHOW_FIRST);
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5" aria-labelledby="support-heading">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="support-heading" className="text-base font-bold text-gray-900">
            민간 지원 소식
          </h2>
          <p className="mt-1 text-xs text-gray-500">
            기업·단체·개인이 피해 복구·구호를 지원했다는 언급을 연결 기사의 제목·발췌에서 규칙으로 뽑았습니다. 보조 근거이며 사건
            상태를 바꾸지 않습니다. 사건 설명에 이름이 있는 주체는 「사건 당사자」로 표시합니다.
          </p>
        </div>
        {result.ok && items.length > 0 && (
          <span className="whitespace-nowrap rounded-full border border-sky-200 bg-sky-50 px-2.5 py-0.5 text-xs font-semibold text-sky-800">
            언급 {result.data.count}건 · 주체 {result.data.actors}곳
          </span>
        )}
      </div>

      {!result.ok ? (
        <p className="mt-3 rounded-md border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
          민간 지원 소식을 불러오지 못했습니다. ({result.error.message})
        </p>
      ) : items.length === 0 ? (
        <p className="mt-3 rounded-md bg-gray-50 px-4 py-3 text-sm text-gray-700">연결 기사에서 확인된 민간 지원 언급이 없습니다</p>
      ) : (
        <>
          <ul className="mt-2 divide-y divide-gray-100">
            {head.map((it) => (
              <SupportRow key={`${it.news_id}:${it.actor}`} item={it} />
            ))}
          </ul>
          {rest.length > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer text-xs font-semibold text-blue-700 hover:underline">나머지 {rest.length}건 보기</summary>
              <ul className="divide-y divide-gray-100">
                {rest.map((it) => (
                  <SupportRow key={`${it.news_id}:${it.actor}`} item={it} />
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      {result.ok && <p className="mt-3 text-[11px] text-gray-400">{result.data.disclaimer}</p>}
    </section>
  );
}
