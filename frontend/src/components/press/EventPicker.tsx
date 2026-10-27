"use client";

import { useRouter } from "next/navigation";
import { pressHref, type PressEventRow, type PressFormat } from "@/lib/press";

/** 사건별 초안 선택 — /press?event_id=N 으로 이동 (빈 값은 전체 초안). 현재 형식(브리프/기사체)은 유지한다. */
export default function EventPicker({ events, selected, format = "brief" }: { events: PressEventRow[]; selected: number | null; format?: PressFormat }) {
  const router = useRouter();
  return (
    <label className="flex min-w-0 flex-col gap-1 text-sm">
      <span className="text-xs font-semibold text-gray-500">사건별 초안</span>
      <select
        className="w-full min-w-0 rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900"
        value={selected ?? ""}
        onChange={(e) => router.push(pressHref(e.target.value ? Number(e.target.value) : null, format))}
      >
        <option value="">사건을 고르세요 (전체 초안 보기)</option>
        {events.map((ev) => (
          <option key={ev.id} value={ev.id}>
            {ev.occurred_at} · {ev.title}
            {ev.status ? ` — ${ev.status}` : ""}
            {ev.has_followup ? " · 후속보도 확인" : ""}
            {ev.notice_found ? " · 기관 발표 확인" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
