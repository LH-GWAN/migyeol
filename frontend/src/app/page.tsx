import Link from "next/link";
import ErrorBox from "@/components/ErrorBox";
import EventCardView from "@/components/EventCardView";
import EventFilters, { type FilterState } from "@/components/EventFilters";
import { getEvents, safe } from "@/lib/api";
import { isEventStatus, isSortKey, STATUSES } from "@/lib/types";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const PAGE_SIZE = 20;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function buildHref(filters: FilterState, page: number): string {
  const qs = new URLSearchParams();
  if (filters.status) qs.set("status", filters.status);
  if (filters.region) qs.set("region", filters.region);
  if (filters.incident_type) qs.set("incident_type", filters.incident_type);
  if (!filters.needs_check) qs.set("needs_check", "false");
  if (filters.sort !== "priority") qs.set("sort", filters.sort);
  if (page > 1) qs.set("page", String(page));
  const s = qs.toString();
  return s ? `/?${s}` : "/";
}

export default async function HomePage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const rawStatus = first(sp.status);
  const rawSort = first(sp.sort);
  const status = isEventStatus(rawStatus) ? rawStatus : undefined;
  const filters: FilterState = {
    status: status ?? "",
    region: first(sp.region) ?? "",
    incident_type: first(sp.incident_type) ?? "",
    needs_check: first(sp.needs_check) !== "false", // default ON
    sort: isSortKey(rawSort) ? rawSort : "priority",
  };
  const page = Math.max(1, Number.parseInt(first(sp.page) ?? "1", 10) || 1);

  const result = await safe(
    getEvents({
      status,
      region: filters.region || undefined,
      incident_type: filters.incident_type || undefined,
      // ON -> only needs_check=true; OFF -> no filter (show everything)
      needs_check: filters.needs_check ? true : undefined,
      sort: filters.sort,
      page,
      size: PAGE_SIZE,
    }),
  );

  const options = result.ok
    ? result.data.filters
    : { statuses: [...STATUSES], regions: [], incident_types: [] };

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">후속 확인 필요 목록</h1>
        <p className="mt-1 text-sm text-gray-600">
          공공기관이 발표한 조치가 이후 보도로 확인되었는지 추적합니다. 우선순위가 높은 사건부터 표시합니다.
        </p>
      </div>

      <EventFilters
        statuses={options.statuses}
        regions={options.regions}
        incidentTypes={options.incident_types}
        current={filters}
      />

      {!result.ok ? (
        <ErrorBox title="사건 목록을 불러오지 못했습니다" error={result.error} />
      ) : (
        <>
          <p className="text-sm text-gray-500">
            총 <span className="font-semibold text-gray-800">{result.data.total}</span>건
            {result.data.total > result.data.size && (
              <>
                {" "}
                · {result.data.page}페이지 ({(result.data.page - 1) * result.data.size + 1}–
                {Math.min(result.data.page * result.data.size, result.data.total)}번째)
              </>
            )}
            {filters.needs_check && <span className="ml-2 text-gray-400">확인 필요 사건만 표시 중</span>}
          </p>

          {result.data.items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-500">
              조건에 맞는 사건이 없습니다.
              {filters.needs_check && (
                <>
                  {" "}
                  <Link href={buildHref({ ...filters, needs_check: false }, 1)} className="text-blue-700 underline">
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
                <Link href={buildHref(filters, result.data.page - 1)} className="text-blue-700 hover:underline">
                  ← 이전
                </Link>
              ) : (
                <span className="text-gray-300">← 이전</span>
              )}
              <span className="text-gray-500">
                {result.data.page} / {Math.max(1, Math.ceil(result.data.total / result.data.size))}
              </span>
              {result.data.page * result.data.size < result.data.total ? (
                <Link href={buildHref(filters, result.data.page + 1)} className="text-blue-700 hover:underline">
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
