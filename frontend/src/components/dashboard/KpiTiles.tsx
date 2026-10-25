import { formatDateTime, formatPercent } from "@/lib/format";
import type { DashboardSummary } from "@/lib/dashboard";

interface Tile {
  label: string;
  value: string;
  unit?: string;
  note?: string;
  accent?: string; // 왼쪽 띠 색
}

/** KPI 타일: 추적 사건 / 후속보도 확인 / 기한 경과 / 기관 발표 확인 / 마지막 갱신 (+ 보도된 인명 피해가 있으면) */
export default function KpiTiles({ s, casualties }: { s: DashboardSummary; casualties: { dead: number | null; injured: number | null } }) {
  const tiles: Tile[] = [
    { label: "추적 중인 사건", value: String(s.events_total), unit: "건", note: `관련 기관 ${s.orgs}곳`, accent: "bg-gray-900" },
    {
      label: "후속보도 확인",
      value: String(s.with_followup),
      unit: "건",
      note: `전체의 ${formatPercent(s.followup_rate, 1)} · 사건 이후 연결 기사 ${s.followup_articles}건`,
      accent: "bg-[#2a78d6]",
    },
    { label: "발표 기한이 지난 사건", value: String(s.overdue), unit: "건", note: "조치 완료가 확인된 사건 제외", accent: "bg-red-500" },
    { label: "기관 발표 확인", value: String(s.with_org_notice), unit: "건", note: "기관 공식 게시판에서 찾은 사건", accent: "bg-violet-500" },
  ];
  if (casualties.dead !== null || casualties.injured !== null) {
    tiles.push({
      label: "보도된 인명 피해",
      value: `${casualties.dead ?? "-"}`,
      unit: "명 사망",
      note: `부상 ${casualties.injured ?? "-"}명 · 기사 제목·발췌 기준`,
      accent: "bg-gray-500",
    });
  }
  tiles.push({
    label: "마지막 갱신",
    value: s.last_run ? formatDateTime(s.last_run).replace(/^\d{4}년 /, "") : "기록 없음",
    note: `기준일 ${s.as_of}`,
    accent: "bg-gray-300",
  });

  return (
    <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      {tiles.map((t) => (
        <li
          key={t.label}
          className={`relative overflow-hidden rounded-xl border border-gray-200 bg-white p-4 shadow-sm ${t.unit ? "" : "col-span-2 md:col-span-1"}`}
        >
          <span className={`absolute inset-y-0 left-0 w-1 ${t.accent ?? "bg-gray-200"}`} aria-hidden />
          <p className="text-xs font-medium text-gray-500">{t.label}</p>
          <p className="mt-1 text-gray-900">
            <span className={`font-bold tabular-nums ${t.unit ? "text-3xl" : "text-lg leading-9"}`}>{t.value}</span>
            {t.unit && <span className="ml-1 text-sm font-medium text-gray-500">{t.unit}</span>}
          </p>
          {t.note && <p className="mt-1 text-[11px] leading-snug text-gray-500">{t.note}</p>}
        </li>
      ))}
    </ul>
  );
}
