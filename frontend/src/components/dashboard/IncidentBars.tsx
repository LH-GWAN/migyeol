import { formatPercent } from "@/lib/format";
import type { IncidentRow } from "@/lib/dashboard";

/**
 * 재난 유형(계열)별 가로 막대: 막대 길이 = 추적 사건 수, 진한 부분 = 그중 후속보도가 확인된 사건.
 * 비율은 막대 끝에 숫자로 적는다 (색만으로 읽지 않게).
 */
export default function IncidentBars({ rows }: { rows: IncidentRow[] }) {
  if (rows.length === 0) return <p className="text-sm text-gray-500">집계할 사건이 없습니다.</p>;
  const max = Math.max(...rows.map((r) => r.events));
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-4 text-xs text-gray-600">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-[#2a78d6]" aria-hidden />
          후속보도 확인
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-[#cde2fb]" aria-hidden />
          확인 전
        </span>
      </div>
      <ul className="flex flex-col gap-3">
        {rows.map((r) => (
          <li key={r.incident_type} title={`${r.incident_type}: 사건 ${r.events}건 중 ${r.with_followup}건 후속보도 확인`}>
            <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
              <span className="font-semibold text-gray-900">
                {r.label}
                <span className="ml-1.5 text-xs font-normal text-gray-400">{r.incident_type.split(">")[0]}</span>
              </span>
              <span className="tabular-nums text-gray-600">
                <b className="text-gray-900">{r.with_followup}</b> / {r.events}건 · {formatPercent(r.rate)}
              </span>
            </div>
            <div className="flex h-3 w-full rounded bg-gray-50">
              <div className="flex h-full gap-[2px]" style={{ width: `${(r.events / max) * 100}%` }}>
                {r.with_followup > 0 && (
                  <div className="h-full rounded-l bg-[#2a78d6] last:rounded-r" style={{ width: `${(r.with_followup / r.events) * 100}%` }} />
                )}
                {r.events - r.with_followup > 0 && (
                  <div className="h-full flex-1 rounded-r bg-[#cde2fb] first:rounded-l" />
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
