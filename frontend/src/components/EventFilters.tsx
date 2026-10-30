"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { DEFAULT_FILTERS, filterKey, filterQuery, type FilterState } from "@/lib/filters";
import { STATUSES, type SortKey } from "@/lib/types";

interface Props {
  statuses: readonly string[];
  regions: readonly string[];
  incidentTypes: readonly string[];
  current: FilterState;
}

const SORT_OPTIONS: Array<{ value: SortKey; label: string }> = [
  { value: "priority", label: "우선순위 높은 순" },
  { value: "recent", label: "최근 후속 기사 순" },
  { value: "occurred", label: "최근 발생 순" },
];

/**
 * List filters kept in URL search params so the list stays shareable and server-rendered.
 * Selects are driven by local state (not the server-provided `current`) so they do not snap back
 * to the old value while the navigation is pending; local state re-syncs when the URL-derived
 * `current` actually changes (back/forward, reset links).
 */
export default function EventFilters({ statuses, regions, incidentTypes, current }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [pending, startTransition] = useTransition();
  const [local, setLocal] = useState<FilterState>(current);
  const [syncedKey, setSyncedKey] = useState(filterKey(current));

  const currentKey = filterKey(current);
  if (currentKey !== syncedKey) {
    setSyncedKey(currentKey);
    setLocal(current);
  }

  const apply = (patch: Partial<FilterState>) => {
    const next: FilterState = { ...local, ...patch };
    setLocal(next);
    const s = filterQuery(next).toString();
    startTransition(() => router.push(s ? `${pathname}?${s}` : pathname));
  };

  const selectClass =
    "rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-800 focus:border-blue-500 focus:outline-none";
  const statusOptions = statuses.length > 0 ? statuses : STATUSES;
  const isDefault = filterKey(local) === "";
  const completedHiddenByToggle =
    local.needs_check && (local.status === "조치 완료 근거 확인" || local.status === "조치 진행 정황 확인" || local.status === "새로운 문제 발생");

  return (
    <div
      className={`flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 ${pending ? "opacity-70" : ""}`}
      aria-busy={pending}
    >
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <label className="flex flex-col gap-1 text-xs text-gray-500">
          상태
          <select className={selectClass} value={local.status} onChange={(e) => apply({ status: e.target.value })}>
            <option value="">전체</option>
            {statusOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs text-gray-500">
          지역
          <select className={selectClass} value={local.region} onChange={(e) => apply({ region: e.target.value })}>
            <option value="">전체</option>
            {regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
            {local.region && !regions.includes(local.region) && <option value={local.region}>{local.region}</option>}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs text-gray-500">
          사건 유형
          <select
            className={selectClass}
            value={local.incident_type}
            onChange={(e) => apply({ incident_type: e.target.value })}
          >
            <option value="">전체</option>
            {incidentTypes.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
            {local.incident_type && !incidentTypes.includes(local.incident_type) && (
              <option value={local.incident_type}>{local.incident_type}</option>
            )}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs text-gray-500">
          정렬
          <select className={selectClass} value={local.sort} onChange={(e) => apply({ sort: e.target.value as SortKey })}>
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>

        <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm text-gray-800">
          <input
            type="checkbox"
            className="h-4 w-4 accent-blue-600"
            checked={local.needs_check}
            onChange={(e) => apply({ needs_check: e.target.checked })}
          />
          확인 필요만 보기
        </label>

        {!isDefault && (
          <button
            type="button"
            onClick={() => apply(DEFAULT_FILTERS)}
            className="text-xs text-gray-500 underline-offset-2 hover:underline"
          >
            초기화
          </button>
        )}
      </div>

      {completedHiddenByToggle && (
        <p className="flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="status">
          확인 필요 토글이 켜져 있어 후속보도가 확인된 사건(완료·진행·새로운 문제)은 표시되지 않습니다
          <button
            type="button"
            onClick={() => apply({ needs_check: false })}
            className="rounded border border-amber-300 bg-white px-2 py-0.5 font-semibold text-amber-900 hover:bg-amber-100"
          >
            토글 끄기
          </button>
        </p>
      )}
    </div>
  );
}
