import type { Metadata } from "next";
import Link from "next/link";
import Disclaimer from "@/components/Disclaimer";
import ErrorBox from "@/components/ErrorBox";
import StatusBadge from "@/components/StatusBadge";
import ArticleBody from "@/components/press/ArticleBody";
import DraftActions from "@/components/press/DraftActions";
import DraftBody from "@/components/press/DraftBody";
import EventPicker from "@/components/press/EventPicker";
import { safe } from "@/lib/api";
import { formatDateTime, parseId } from "@/lib/format";
import { draftFileName, getPressDraft, getPressEvents, parsePressFormat, pressHref, type PressDraft, type PressFormat } from "@/lib/press";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "보도자료 초안" };

type SearchParams = Promise<{ event_id?: string | string[]; format?: string | string[] }>;

const FORMAT_TABS: { key: PressFormat; label: string; hint: string }[] = [
  { key: "brief", label: "사건 브리프", hint: "절·목록으로 정리한 자료형 초안" },
  { key: "article", label: "기사체", hint: "제목·부제·리드·본문으로 이어 쓴 기사형 초안" },
];

const HEADER_NOTE = "이 초안은 미결이 빅카인즈 API로 수집·분석한 결과를 바탕으로 자동 작성했습니다. 배포 전 사람이 검토합니다.";

function StatTiles({ draft }: { draft: PressDraft }) {
  const s = draft.stats;
  const tiles =
    draft.scope === "all"
      ? [
          { label: "원천 기사", value: s.source_articles },
          { label: "추적 사건", value: s.events },
          { label: "후속보도 확인", value: s.with_followup },
          {
            label: "후속보도 확인 비율",
            value: typeof s.followup_rate === "number" ? `${(s.followup_rate * 100).toFixed(1)}%` : "-",
          },
        ]
      : [
          { label: "발표된 약속", value: s.promises },
          { label: "후속보도 근거", value: s.followup_signals },
          { label: "기관 발표 확인", value: s.notice_items },
        ];
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
          <dt className="text-xs text-gray-500">{t.label}</dt>
          <dd className="text-xl font-bold tabular-nums text-gray-900">{typeof t.value === "number" ? t.value.toLocaleString("ko-KR") : (t.value ?? "-")}</dd>
        </div>
      ))}
    </dl>
  );
}

export default async function PressPage({ searchParams }: { searchParams: SearchParams }) {
  const sp = await searchParams;
  const raw = Array.isArray(sp.event_id) ? sp.event_id[0] : sp.event_id;
  const eventId = raw ? parseId(raw) : null;
  const invalidId = Boolean(raw) && eventId === null;
  const format = parsePressFormat(sp.format);

  const [draftRes, eventsRes] = await Promise.all([
    invalidId ? null : safe(getPressDraft(eventId, format)),
    safe(getPressEvents()),
  ]);
  const events = eventsRes.ok ? eventsRes.data : [];
  const selected = eventId !== null ? events.find((e) => e.id === eventId) : undefined;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <p className="text-sm font-semibold text-gray-500">보도자료 초안 · 누구나 볼 수 있음</p>
        <h1 className="text-2xl font-bold leading-tight text-gray-900 sm:text-3xl">끝나지 않은 뉴스, 보도자료로 알립니다</h1>
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-950">
          <p className="font-semibold">{HEADER_NOTE}</p>
          <p className="mt-1 text-amber-900/80">
            숫자와 사례는 모두 근거 기사 번호([1], [2] …)로 되돌아갈 수 있고, 기관의 조치 여부를 판정하지 않으며
            &ldquo;확인됐다 / 확인되지 않았다&rdquo;로만 씁니다. 후속보도가 확인된 기관은 이름을 밝혀 적습니다.
          </p>
        </div>
      </header>

      <nav className="flex flex-col gap-2 rounded-lg border border-gray-200 bg-white p-4" aria-label="초안 형식">
        <p className="text-xs font-semibold text-gray-500">초안 형식</p>
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="초안 형식 선택">
          {FORMAT_TABS.map((t) => (
            <Link
              key={t.key}
              href={pressHref(eventId, t.key)}
              role="tab"
              aria-selected={format === t.key}
              title={t.hint}
              className={`rounded-md px-4 py-2 text-sm font-semibold ${format === t.key ? "bg-blue-700 text-white" : "border border-gray-300 text-gray-700 hover:bg-gray-50"}`}
            >
              {t.label}
            </Link>
          ))}
        </div>
        <p className="text-xs text-gray-500">{FORMAT_TABS.find((t) => t.key === format)?.hint} · 두 형식 모두 같은 집계와 근거 번호를 씁니다.</p>
      </nav>

      <nav className="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 sm:flex-row sm:items-end sm:justify-between" aria-label="초안 선택">
        <div className="flex flex-wrap gap-2" role="tablist">
          <Link
            href={pressHref(null, format)}
            role="tab"
            aria-selected={eventId === null}
            className={`rounded-full px-3 py-1.5 text-sm font-semibold ${eventId === null ? "bg-gray-900 text-white" : "border border-gray-300 text-gray-700 hover:bg-gray-50"}`}
          >
            전체 초안
          </Link>
          <span
            role="tab"
            aria-selected={eventId !== null}
            className={`rounded-full px-3 py-1.5 text-sm font-semibold ${eventId !== null ? "bg-gray-900 text-white" : "border border-dashed border-gray-300 text-gray-500"}`}
          >
            사건별 초안{selected ? ` · #${selected.id}` : ""}
          </span>
        </div>
        <div className="w-full sm:max-w-md">
          <EventPicker events={events} selected={eventId} format={format} />
        </div>
      </nav>

      {invalidId ? (
        <ErrorBox title="사건 번호가 올바르지 않습니다" message={`'${raw}' 는 사건 번호가 아닙니다.`} hint="목록에서 사건을 다시 골라 주세요." />
      ) : draftRes && !draftRes.ok ? (
        <ErrorBox
          title={eventId ? `사건 #${eventId}의 보도자료 초안을 만들지 못했습니다` : "보도자료 초안을 만들지 못했습니다"}
          error={draftRes.error}
        />
      ) : draftRes && draftRes.ok ? (
        <article className="flex flex-col gap-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm sm:p-8">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-1">
              <p className="text-xs font-semibold tracking-wide text-gray-500">
                {draftRes.data.scope === "all" ? "보도자료 (전체)" : "보도자료 (사건별)"} · {draftRes.data.format === "article" ? "기사체" : "사건 브리프"} 초안
              </p>
              {draftRes.data.format === "article" ? (
                <>
                  <h2 className="text-2xl font-extrabold leading-snug tracking-tight text-gray-900 sm:text-3xl">{draftRes.data.title}</h2>
                  {draftRes.data.subtitle && <p className="text-base font-semibold leading-relaxed text-gray-700">{draftRes.data.subtitle}</p>}
                </>
              ) : (
                <h2 className="text-xl font-bold leading-snug text-gray-900 sm:text-2xl">{draftRes.data.title}</h2>
              )}
              <p className="text-xs text-gray-500">
                작성 {formatDateTime(draftRes.data.generated_at)} · 작성기 {draftRes.data.writer}
                {draftRes.data.scope === "all" &&
                  (draftRes.data.data_source === "experiment"
                    ? ` · 숫자 출처: 실험 리포트(${(draftRes.data.experiment_generated_at ?? "").slice(0, 10)})`
                    : " · 숫자 출처: 현재 DB 집계")}
              </p>
            </div>
            <DraftActions markdown={draftRes.data.markdown} fileName={draftFileName(draftRes.data)} />
          </div>
          {selected?.status && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StatusBadge status={selected.status} />
              <Link href={`/events/${selected.id}`} className="text-blue-700 hover:underline">
                사건 타임라인
              </Link>
              <span className="text-gray-300">·</span>
              <Link href={`/events/${selected.id}/evidence`} className="text-blue-700 hover:underline">
                근거·기관 발표 확인
              </Link>
            </div>
          )}
          <StatTiles draft={draftRes.data} />
          <hr className="border-gray-100" />
          {draftRes.data.format === "article" ? (
            <ArticleBody sections={draftRes.data.sections} sources={draftRes.data.sources} />
          ) : (
            <DraftBody sections={draftRes.data.sections} sources={draftRes.data.sources} />
          )}
          <Disclaimer text={draftRes.data.disclaimer} className="border-t border-gray-100 pt-3" />
        </article>
      ) : null}

      {!eventsRes.ok && <ErrorBox title="사건 목록을 불러오지 못했습니다" error={eventsRes.error} />}
    </div>
  );
}
