"use client";

import { usePathname, useRouter } from "next/navigation";
import { useTransition } from "react";
import { STATUSES, type SortKey } from "@/lib/types";

export interface FilterState {
  status: string;
  region: string;
  incident_type: string;
  needs_check: boolean;
  sort: SortKey;
}

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

/** List filters kept in URL search params so the list stays shareable and server-rendered. */
export default function EventFilters({ statuses, regions, incidentTypes, current }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [pending, startTransition] = useTransition();

  const apply = (patch: Partial<FilterState>) => {
    const next: FilterState = { ...current, ...patch };
    const qs = new URLSearchParams();
    if (next.status) qs.set("status", next.status);
    if (next.region) qs.set("region", next.region);
    if (next.incident_type) qs.set("incident_type", next.incident_type);
    if (!next.needs_check) qs.set("needs_check", "false"); // default (absent) = true
    if (next.sort !== "priority") qs.set("sort", next.sort);
    const s = qs.toString();
    startTransition(() => router.push(s ? `${pathname}?${s}` : pathname));
  };

  const selectClass =
    "rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-800 focus:border-blue-500 focus:outline-none";
  const statusOptions = statuses.length > 0 ? statuses : STATUSES;

  return (
    <div
      className={`flex flex-wrap items-end gap-x-4 gap-y-3 rounded-lg border border-gray-200 bg-white p-4 ${pending ? "opacity-70" : ""}`}
      aria-busy={pending}
    >
      <label className="flex flex-col gap-1 text-xs text-gray-500">
        상태
        <select className={selectClass} value={current.status} onChange={(e) => apply({ status: e.target.value })}>
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
        <select className={selectClass} value={current.region} onChange={(e) => apply({ region: e.target.value })}>
          <option value="">전체</option>
          {regions.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
          {current.region && !regions.includes(current.region) && (
            <option value={current.region}>{current.region}</option>
          )}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-gray-500">
        사건 유형
        <select
          className={selectClass}
          value={current.incident_type}
          onChange={(e) => apply({ incident_type: e.target.value })}
        >
          <option value="">전체</option>
          {incidentTypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
          {current.incident_type && !incidentTypes.includes(current.incident_type) && (
            <option value={current.incident_type}>{current.incident_type}</option>
          )}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-gray-500">
        정렬
        <select
          className={selectClass}
          value={current.sort}
          onChange={(e) => apply({ sort: e.target.value as SortKey })}
        >
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
          checked={current.needs_check}
          onChange={(e) => apply({ needs_check: e.target.checked })}
        />
        확인 필요만 보기
      </label>

      {(current.status || current.region || current.incident_type || !current.needs_check || current.sort !== "priority") && (
        <button
          type="button"
          onClick={() => apply({ status: "", region: "", incident_type: "", needs_check: true, sort: "priority" })}
          className="text-xs text-gray-500 underline-offset-2 hover:underline"
        >
          초기화
        </button>
      )}
    </div>
  );
}
