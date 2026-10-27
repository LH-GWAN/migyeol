import Link from "next/link";
import { formatDate, formatPercent } from "@/lib/format";
import type { OrgRow, OrgSort } from "@/lib/dashboard";

/**
 * 기관별 후속보도 현황. 정렬 기본 = 후속보도 확인 건수 내림차순(동률이면 비율) — 서버가 정렬해 준 순서 그대로.
 * 후속보도가 확인된 기관을 위 묶음, 아직 없는 기관을 아래 묶음으로 나눈다. 기관명 → /events?org=
 */

const SORTS: Array<{ key: OrgSort; label: string }> = [
  { key: "followup", label: "확인 건수순" },
  { key: "rate", label: "비율순" },
  { key: "events", label: "사건 수순" },
];

function orgHref(org: string): string {
  return `/events?org=${encodeURIComponent(org)}&needs_check=false`;
}

function Rows({ rows, rankStart }: { rows: OrgRow[]; rankStart: number | null }) {
  return (
    <>
      {rows.map((r, i) => (
        <tr key={r.org} className="border-t border-gray-100 hover:bg-gray-50/70">
          <td className="hidden w-8 py-2.5 pl-3 text-xs tabular-nums text-gray-400 sm:table-cell">{rankStart !== null ? rankStart + i : ""}</td>
          <td className="py-2.5 pl-3 pr-2 sm:pl-0">
            <Link href={orgHref(r.org)} className="font-semibold text-gray-900 hover:text-blue-700 hover:underline">
              {r.org}
            </Link>
            {r.raw_names && r.raw_names.length > 0 && r.raw_names.some((n) => n !== r.org) && (
              <span className="block text-[11px] text-gray-400">{r.raw_names.join(" · ")}</span>
            )}
          </td>
          <td className="py-2.5 pr-2 text-right tabular-nums text-gray-700">{r.events}</td>
          <td className="py-2.5 pr-2 text-right tabular-nums">
            <b className={r.with_followup > 0 ? "text-gray-900" : "text-gray-400"}>{r.with_followup}</b>
          </td>
          <td className="py-2.5 pr-3">
            <div className="flex items-center justify-end gap-2">
              <div className="hidden h-1.5 w-20 overflow-hidden rounded-full bg-[#cde2fb] sm:block" aria-hidden>
                <div className="h-full rounded-full bg-[#2a78d6]" style={{ width: `${r.rate * 100}%` }} />
              </div>
              <span className="w-10 text-right tabular-nums text-gray-700">{formatPercent(r.rate)}</span>
            </div>
          </td>
          <td className="hidden py-2.5 pr-3 text-right text-xs text-gray-500 md:table-cell">
            {r.latest_followup_at ? formatDate(r.latest_followup_at) : "-"}
          </td>
        </tr>
      ))}
    </>
  );
}

/**
 * limit 가 있으면(대시보드 요약) 서버 정렬 순서대로 앞 limit 곳만, 정렬 버튼·묶음 머리 없이 보여 준다.
 * 전체 표(/orgs)는 limit 없이 — 정렬 버튼과 "확인된 기관 / 아직 없는 기관" 묶음을 보여 준다.
 */
export default function OrgTable({ rows, sort, limit }: { rows: OrgRow[]; sort: OrgSort; limit?: number }) {
  const compact = typeof limit === "number";
  const shown = compact ? rows.slice(0, limit) : rows;
  const confirmed = shown.filter((r) => r.with_followup > 0);
  const none = shown.filter((r) => r.with_followup === 0);
  const groupHead = (label: string, count: number) => (
    <tr className="bg-gray-50">
      <td colSpan={6} className="px-3 py-1.5 text-xs font-semibold text-gray-600">
        {label} <span className="font-normal text-gray-400">{count}곳</span>
      </td>
    </tr>
  );
  return (
    <div className="flex flex-col gap-3">
      {!compact && (
      <nav className="flex flex-wrap gap-1 text-xs" aria-label="기관 정렬">
        {SORTS.map((s) => (
          <Link
            key={s.key}
            href={s.key === "followup" ? "/orgs" : `/orgs?sort=${s.key}`}
            scroll={false}
            className={`rounded-md px-2.5 py-1 font-semibold ${sort === s.key ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"}`}
            aria-current={sort === s.key ? "true" : undefined}
          >
            {s.label}
          </Link>
        ))}
      </nav>
      )}
      {rows.length === 0 ? (
        <p className="text-sm text-gray-500">집계할 기관이 없습니다.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-white text-xs text-gray-500">
              <tr>
                <th className="hidden py-2 pl-3 text-left font-medium sm:table-cell" />
                <th className="py-2 pl-3 pr-2 text-left font-medium sm:pl-0">기관</th>
                <th className="py-2 pr-2 text-right font-medium">사건</th>
                <th className="py-2 pr-2 text-right font-medium">
                  <span className="sm:hidden">확인</span>
                  <span className="hidden sm:inline">후속보도 확인</span>
                </th>
                <th className="py-2 pr-3 text-right font-medium">비율</th>
                <th className="hidden py-2 pr-3 text-right font-medium md:table-cell">최근 확인</th>
              </tr>
            </thead>
            <tbody>
              {compact ? (
                <Rows rows={shown} rankStart={1} />
              ) : (
                <>
                  {confirmed.length > 0 && groupHead("후속보도가 확인된 기관", confirmed.length)}
                  <Rows rows={confirmed} rankStart={1} />
                  {none.length > 0 && groupHead("아직 후속보도가 확인되지 않은 기관", none.length)}
                  <Rows rows={none} rankStart={null} />
                </>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
