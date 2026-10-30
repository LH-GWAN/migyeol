import Link from "next/link";
import Disclaimer from "@/components/Disclaimer";
import ErrorBox from "@/components/ErrorBox";
import EventCardView from "@/components/EventCardView";
import EventFilters from "@/components/EventFilters";
import { safe } from "@/lib/api";
import { getByOrg, getEventsWithOrg } from "@/lib/dashboard";
import { filterQuery, type FilterState } from "@/lib/filters";
import { isEventStatus, isSortKey, STATUSES, type EventCard, type EventStatus, type SortKey } from "@/lib/types";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;
type Tab = "check" | "followup";

const PAGE_SIZE = 20;
/** `후속보도 확인됨` 탭 = 이 세 상태 — 대시보드 '후속보도 확인' 과 같은 기준 (2026-10-30, 백엔드 config.FOLLOWUP_STATUSES) */
const FOLLOWUP_STATUSES: readonly EventStatus[] = ["조치 완료 근거 확인", "조치 진행 정황 확인", "새로운 문제 발생"];
const SORT_LABELS: Array<{ value: SortKey; label: string }> = [
  { value: "priority", label: "우선순위순" },
  { value: "recent", label: "최근 후속 기사순" },
  { value: "occurred", label: "최근 발생순" },
];

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function tabHref(tab: Tab, org: string, sort?: SortKey): string {
  const qs = new URLSearchParams();
  if (tab === "followup") qs.set("tab", "followup");
  if (org) qs.set("org", org);
  if (sort && sort !== "priority") qs.set("sort", sort);
  const s = qs.toString();
  return s ? `/events?${s}` : "/events";
}

function buildHref(filters: FilterState, page: number, org: string): string {
  const qs = filterQuery(filters);
  if (org) qs.set("org", org);
  if (page > 1) qs.set("page", String(page));
  const s = qs.toString();
  return s ? `/events?${s}` : "/events";
}

function Tabs({ tab, org, counts }: { tab: Tab; org: string; counts: Partial<Record<Tab, number>> }) {
  const item = (key: Tab, label: string) => (
    <Link
      href={tabHref(key, org)}
      aria-current={tab === key ? "page" : undefined}
      className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-semibold ${
        tab === key ? "border-gray-900 text-gray-900" : "border-transparent text-gray-500 hover:text-gray-800"
      }`}
    >
      {label}
      {counts[key] !== undefined && (
        <span className={`rounded-full px-1.5 text-xs ${tab === key ? "bg-gray-900 text-white" : "bg-gray-100 text-gray-600"}`}>{counts[key]}</span>
      )}
    </Link>
  );
  return (
    <nav className="flex border-b border-gray-200" aria-label="목록 보기">
      {item("check", "확인 필요")}
      {item("followup", "후속보도 확인됨")}
    </nav>
  );
}

/** 기관 필터 칩. 백엔드가 아직 org 를 거르지 않으면(통합 전) 섞여 있다고 알린다 — 프런트에서 따로 거르지 않는다. */
function OrgChip({ org, tab, rawNames, cards }: { org: string; tab: Tab; rawNames: string[] | null; cards: EventCard[] }) {
  const mixed = rawNames !== null && cards.some((c) => !rawNames.includes(c.responsible_org));
  return (
    <div className="flex flex-col gap-2">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 font-semibold text-blue-800">
          기관: {org}
          <Link href={tabHref(tab, "")} className="text-blue-500 hover:text-blue-800" aria-label="기관 필터 해제">
            ✕
          </Link>
        </span>
        {rawNames && rawNames.some((n) => n !== org) && <span className="text-xs text-gray-500">({rawNames.join(" · ")} 포함)</span>}
      </p>
      {mixed && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="status">
          기관별 보기는 준비 중입니다. 지금은 다른 기관의 사건도 함께 표시됩니다.
        </p>
      )}
    </div>
  );
}

export default async function EventsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const tab: Tab = first(sp.tab) === "followup" ? "followup" : "check";
  const org = (first(sp.org) ?? "").trim();
  const rawSort = first(sp.sort);
  const sort: SortKey = isSortKey(rawSort) ? rawSort : "priority";
  const orgRows = org ? await safe(getByOrg()) : null;
  const rawNames = orgRows?.ok ? (orgRows.data.find((r) => r.org === org)?.raw_names ?? [org]) : null;

  const header = (
    <div>
      <h1 className="text-2xl font-bold text-gray-900">사건 목록</h1>
      <p className="mt-1 text-sm text-gray-600">
        공공기관이 발표한 조치가 이후 보도로 확인되었는지 추적합니다. 확인이 필요한 사건을 먼저 보여 줍니다.
      </p>
    </div>
  );

  if (tab === "followup") {
    const results = await Promise.all(
      FOLLOWUP_STATUSES.map((status) => safe(getEventsWithOrg({ status, org: org || undefined, sort, size: 100 }))),
    );
    const total = results.reduce((n, r) => n + (r.ok ? r.data.total : 0), 0);
    const allCards = results.flatMap((r) => (r.ok ? r.data.items : []));
    return (
      <div className="flex flex-col gap-5">
        {header}
        <Tabs tab={tab} org={org} counts={{ followup: total }} />
        {org && <OrgChip org={org} tab={tab} rawNames={rawNames} cards={allCards} />}
        <div className="flex flex-wrap items-center gap-1 text-xs">
          <span className="mr-1 text-gray-500">정렬</span>
          {SORT_LABELS.map((o) => (
            <Link
              key={o.value}
              href={tabHref("followup", org, o.value)}
              className={`rounded-md px-2.5 py-1 font-semibold ${sort === o.value ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"}`}
            >
              {o.label}
            </Link>
          ))}
        </div>
        <p className="text-sm text-gray-600">
          후속 기사에서 조치 완료·진행 근거나 새로운 문제가 확인된 사건입니다. 확인된 근거는 사건을 눌러 볼 수 있습니다.
        </p>
        {FOLLOWUP_STATUSES.map((status, i) => {
          const r = results[i];
          return (
            <section key={status} className="flex flex-col gap-3">
              <h2 className="flex items-center gap-2 text-base font-bold text-gray-900">
                {status}
                {r.ok && <span className="text-sm font-normal text-gray-500">{r.data.total}건</span>}
              </h2>
              {!r.ok ? (
                <ErrorBox title={`${status} 사건을 불러오지 못했습니다`} error={r.error} />
              ) : r.data.items.length === 0 ? (
                <p className="rounded-lg border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-500">해당 사건이 없습니다.</p>
              ) : (
                <>
                  <ul className="flex flex-col gap-4">
                    {r.data.items.map((card) => (
                      <li key={card.id}>
                        <EventCardView card={card} />
                      </li>
                    ))}
                  </ul>
                  {r.data.total > r.data.items.length && (
                    <Link
                      href={`/events?status=${encodeURIComponent(status)}&needs_check=false${org ? `&org=${encodeURIComponent(org)}` : ""}`}
                      className="text-sm text-blue-700 hover:underline"
                    >
                      {status} 전체 {r.data.total}건 보기 →
                    </Link>
                  )}
                </>
              )}
            </section>
          );
        })}
        <Disclaimer />
      </div>
    );
  }

  const rawStatus = first(sp.status);
  const status = isEventStatus(rawStatus) ? rawStatus : undefined;
  const filters: FilterState = {
    status: status ?? "",
    region: first(sp.region) ?? "",
    incident_type: first(sp.incident_type) ?? "",
    needs_check: first(sp.needs_check) !== "false", // default ON
    sort,
  };
  const page = Math.max(1, Number.parseInt(first(sp.page) ?? "1", 10) || 1);

  const result = await safe(
    getEventsWithOrg({
      status,
      region: filters.region || undefined,
      incident_type: filters.incident_type || undefined,
      // ON -> only needs_check=true; OFF -> no filter (show everything)
      needs_check: filters.needs_check ? true : undefined,
      org: org || undefined,
      sort: filters.sort,
      page,
      size: PAGE_SIZE,
    }),
  );

  const options = result.ok ? result.data.filters : { statuses: [...STATUSES], regions: [], incident_types: [] };

  // Range text is computed from what was actually returned, not from page*size.
  const rangeStart = result.ok ? (result.data.page - 1) * result.data.size + 1 : 0;
  const rangeEnd = result.ok ? rangeStart + result.data.items.length - 1 : 0;
  const outOfRange = result.ok && result.data.items.length === 0 && result.data.total > 0;

  return (
    <div className="flex flex-col gap-5">
      {header}
      <Tabs tab={tab} org={org} counts={result.ok && filters.needs_check && !status ? { check: result.data.total } : {}} />
      {org && <OrgChip org={org} tab={tab} rawNames={rawNames} cards={result.ok ? result.data.items : []} />}

      <EventFilters statuses={options.statuses} regions={options.regions} incidentTypes={options.incident_types} current={filters} />

      {!result.ok ? (
        <ErrorBox title="사건 목록을 불러오지 못했습니다" error={result.error} />
      ) : (
        <>
          <p className="text-sm text-gray-500">
            총 <span className="font-semibold text-gray-800">{result.data.total}</span>건
            {result.data.total > result.data.size && result.data.items.length > 0 && (
              <>
                {" "}
                · {result.data.page}페이지 ({rangeStart}–{rangeEnd}번째)
              </>
            )}
            {filters.needs_check && <span className="ml-2 text-gray-400">확인 필요 사건만 표시 중</span>}
          </p>

          {outOfRange ? (
            <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-500">
              페이지 범위를 벗어났습니다.{" "}
              <Link href={buildHref(filters, 1, org)} className="text-blue-700 underline">
                1페이지로 이동
              </Link>
            </div>
          ) : result.data.items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-500">
              조건에 맞는 사건이 없습니다.
              {filters.needs_check && (
                <>
                  {" "}
                  <Link href={buildHref({ ...filters, needs_check: false }, 1, org)} className="text-blue-700 underline">
                    확인 필요 조건을 해제하고 전체 보기
                  </Link>
                </>
              )}
            </div>
          ) : (
            <ul className="flex flex-col gap-4">
              {result.data.items.map((card) => (
                <li key={card.id}>
                  <EventCardView card={card} />
                </li>
              ))}
            </ul>
          )}

          {result.data.total > result.data.size && (
            <nav className="flex items-center justify-center gap-4 text-sm" aria-label="페이지 이동">
              {result.data.page > 1 ? (
                <Link href={buildHref(filters, result.data.page - 1, org)} className="text-blue-700 hover:underline">
                  ← 이전
                </Link>
              ) : (
                <span className="text-gray-300">← 이전</span>
              )}
              <span className="text-gray-500">
                {result.data.page} / {Math.max(1, Math.ceil(result.data.total / result.data.size))}
              </span>
              {result.data.page * result.data.size < result.data.total ? (
                <Link href={buildHref(filters, result.data.page + 1, org)} className="text-blue-700 hover:underline">
                  다음 →
                </Link>
              ) : (
                <span className="text-gray-300">다음 →</span>
              )}
            </nav>
          )}
        </>
      )}
    </div>
  );
}
