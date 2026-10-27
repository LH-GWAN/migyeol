import type { Metadata } from "next";
import Link from "next/link";
import ErrorBox from "@/components/ErrorBox";
import OrgTable from "@/components/dashboard/OrgTable";
import Panel from "@/components/dashboard/Panel";
import { safe } from "@/lib/api";
import { getByOrg, getDashboardSummary, type OrgSort } from "@/lib/dashboard";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "기관별 후속보도 현황" };

type SearchParams = Record<string, string | string[] | undefined>;
const ORG_SORTS: readonly OrgSort[] = ["followup", "rate", "events"];

/** 대시보드에서 분리한 기관별 전체 표 (2026-10-27 피드백: 대시보드에는 5곳만, 나머지는 이 화면). */
export default async function OrgsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const raw = Array.isArray(sp.sort) ? sp.sort[0] : sp.sort;
  const sort: OrgSort = ORG_SORTS.includes(raw as OrgSort) ? (raw as OrgSort) : "followup";
  const [summary, orgs] = await Promise.all([safe(getDashboardSummary()), safe(getByOrg(sort))]);
  const disclaimer = summary.ok ? summary.data.disclaimer : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-xs text-gray-500">
            <Link href="/" className="hover:underline">
              대시보드
            </Link>{" "}
            › 기관별 후속보도 현황
          </p>
          <h1 className="mt-1 text-2xl font-bold text-gray-900">기관별 후속보도 현황</h1>
        </div>
        {summary.ok && (
          <p className="text-xs text-gray-500">
            관련 기관 {summary.data.orgs}곳 · 기준일 {summary.data.as_of}
          </p>
        )}
      </div>
      <Panel
        title="책임 기관별로 묶은 사건과 후속보도"
        subtitle="후속보도가 확인된 기관이 위에 옵니다. 기관명을 누르면 그 기관의 사건만 봅니다."
        disclaimer={disclaimer}
      >
        {orgs.ok ? <OrgTable rows={orgs.data} sort={sort} /> : <ErrorBox title="기관별 집계를 불러오지 못했습니다" error={orgs.error} />}
      </Panel>
    </div>
  );
}
