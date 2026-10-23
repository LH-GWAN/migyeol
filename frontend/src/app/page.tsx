import Link from "next/link";
import ErrorBox from "@/components/ErrorBox";
import Hero from "@/components/dashboard/Hero";
import IncidentBars from "@/components/dashboard/IncidentBars";
import KpiTiles from "@/components/dashboard/KpiTiles";
import NeedsCheckTop from "@/components/dashboard/NeedsCheckTop";
import OrgTable from "@/components/dashboard/OrgTable";
import Panel from "@/components/dashboard/Panel";
import RecentFeed from "@/components/dashboard/RecentFeed";
import StatusBreakdown from "@/components/dashboard/StatusBreakdown";
import TimeseriesChart from "@/components/dashboard/TimeseriesChart";
import { getEvents, safe } from "@/lib/api";
import {
  getByIncident,
  getByOrg,
  getDashboardSummary,
  getRecentFollowups,
  getTimeseries,
  type OrgSort,
  type TimeseriesRow,
} from "@/lib/dashboard";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

const ORG_SORTS: readonly OrgSort[] = ["followup", "rate", "events"];

function sumOrNull(rows: TimeseriesRow[], key: "dead" | "injured"): number | null {
  const vals = rows.map((r) => r[key]).filter((v): v is number => typeof v === "number");
  return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
}

/** v2 화면 0 — 시민용 대시보드. 숫자는 전부 /api/dashboard/* 와 /api/events 에서 온다. */
export default async function DashboardPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const rawSort = Array.isArray(sp.org_sort) ? sp.org_sort[0] : sp.org_sort;
  const orgSort: OrgSort = ORG_SORTS.includes(rawSort as OrgSort) ? (rawSort as OrgSort) : "followup";

  const [summary, month, day, incidents, orgs, recent, top] = await Promise.all([
    safe(getDashboardSummary()),
    safe(getTimeseries("month")),
    safe(getTimeseries("day")),
    safe(getByIncident()),
    safe(getByOrg(orgSort)),
    safe(getRecentFollowups(10)),
    safe(getEvents({ needs_check: true, sort: "priority", size: 5 })),
  ]);

  if (!summary.ok) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-bold text-gray-900">대시보드</h1>
        <ErrorBox title="대시보드 집계를 불러오지 못했습니다" error={summary.error} />
        <Link href="/events" className="text-sm text-blue-700 hover:underline">
          사건 목록으로 →
        </Link>
      </div>
    );
  }
  const s = summary.data;
  const casualties = month.ok ? { dead: sumOrNull(month.data, "dead"), injured: sumOrNull(month.data, "injured") } : { dead: null, injured: null };

  return (
    <div className="flex flex-col gap-6">
      <Hero s={s} />

      <KpiTiles s={s} casualties={casualties} />

      <Panel
        title="언제 일어났고, 언제 다시 보도됐나"
        subtitle="사건 발생 수(막대)와 그 사건의 후속보도 기사 수(선). 그래프 위에 마우스를 올리면(휴대폰은 누르면) 자세히 보입니다."
        disclaimer={s.disclaimer}
      >
        {month.ok && day.ok ? (
          <TimeseriesChart month={month.data} day={day.data} asOf={s.as_of} />
        ) : (
          <ErrorBox title="시계열을 불러오지 못했습니다" error={!month.ok ? month.error : !day.ok ? day.error : null} />
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title="재난 유형별 후속보도" subtitle="유형별 추적 사건 수와 그중 후속보도가 확인된 사건" disclaimer={s.disclaimer}>
          {incidents.ok ? <IncidentBars rows={incidents.data} /> : <ErrorBox title="유형별 집계를 불러오지 못했습니다" error={incidents.error} />}
        </Panel>
        <Panel title="사건 상태" subtitle="후속 기사에서 확인한 근거에 따라 다섯 가지로 표시합니다" disclaimer={s.disclaimer}>
          <StatusBreakdown byStatus={s.by_status} />
        </Panel>
      </div>

      <Panel
        id="orgs"
        title="기관별 후속보도 현황"
        subtitle="책임 기관별로 묶었습니다. 후속보도가 확인된 기관이 위에 옵니다. 기관명을 누르면 그 기관의 사건만 봅니다."
        disclaimer={s.disclaimer}
      >
        {orgs.ok ? <OrgTable rows={orgs.data} sort={orgSort} /> : <ErrorBox title="기관별 집계를 불러오지 못했습니다" error={orgs.error} />}
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Panel className="lg:col-span-3" title="최근 후속보도" subtitle="조치 완료·진행 또는 새로운 문제를 전한 최신 기사" disclaimer={s.disclaimer}>
          {recent.ok ? <RecentFeed items={recent.data} /> : <ErrorBox title="최근 후속보도를 불러오지 못했습니다" error={recent.error} />}
        </Panel>
        <Panel
          className="lg:col-span-2"
          title="지금 확인이 필요한 사건"
          subtitle="후속보도 공백이 길거나 발표 기한이 지난 사건부터"
          disclaimer={s.disclaimer}
          action={
            <Link href="/events" className="text-xs font-semibold text-blue-700 hover:underline">
              더 보기 →
            </Link>
          }
        >
          {top.ok ? <NeedsCheckTop cards={top.data.items} /> : <ErrorBox title="사건 목록을 불러오지 못했습니다" error={top.error} />}
        </Panel>
      </div>
    </div>
  );
}
