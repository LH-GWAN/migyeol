import Link from "next/link";
import { STATUSES, type EventStatus } from "@/lib/types";

/** 5개 상태 분포 — 한 줄 누적 막대 + 범례(상태 배지와 같은 색). 상태별로 /events 필터로 이동한다. */
const FILL: Record<EventStatus, string> = {
  "조치 완료 근거 확인": "bg-green-500",
  "조치 진행 정황 확인": "bg-blue-500",
  "새로운 문제 발생": "bg-orange-500",
  "후속보도 부족": "bg-gray-400",
  "판단 불가": "bg-[repeating-linear-gradient(135deg,#9ca3af_0_3px,#f3f4f6_3px_6px)]",
};

export default function StatusBreakdown({ byStatus }: { byStatus: Record<string, number> }) {
  const total = STATUSES.reduce((s, k) => s + (byStatus[k] ?? 0), 0);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-4 w-full gap-[2px] overflow-hidden rounded-md bg-gray-100" role="img" aria-label="사건 상태 분포">
        {total > 0 &&
          STATUSES.map((s) =>
            byStatus[s] ? (
              <div
                key={s}
                className={`h-full ${FILL[s]} first:rounded-l-md last:rounded-r-md`}
                style={{ width: `${(byStatus[s] / total) * 100}%` }}
                title={`${s} ${byStatus[s]}건`}
              />
            ) : null,
          )}
      </div>
      <ul className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-2">
        {STATUSES.map((s) => (
          <li key={s}>
            <Link
              href={`/events?status=${encodeURIComponent(s)}&needs_check=false`}
              className="flex items-center gap-2 rounded px-1 py-0.5 text-gray-700 hover:bg-gray-50"
            >
              <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-sm ${FILL[s]}`} aria-hidden />
              <span className="flex-1">{s}</span>
              <b className="tabular-nums text-gray-900">{byStatus[s] ?? 0}</b>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
