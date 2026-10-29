import type { Metadata } from "next";
import Link from "next/link";
import ErrorBox from "@/components/ErrorBox";
import NoticeCases from "@/components/dashboard/NoticeCases";
import OrgTabs from "@/components/dashboard/OrgTabs";
import Panel from "@/components/dashboard/Panel";
import { safe } from "@/lib/api";
import { getDashboardSummary, getNoticeCases } from "@/lib/dashboard";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "기관 발표로 확인된 사례" };

/** 「기관」 메뉴 둘째 항목 (2026-10-29 피드백): 대시보드 패널로만 있던 기관 발표 사례를 별도 화면으로. 종류별 최대 5건, 숫자는 /api/dashboard/notice-cases 그대로. */
const PER_KIND = 5;

export default async function OrgNoticesPage() {
  const [summary, cases] = await Promise.all([safe(getDashboardSummary()), safe(getNoticeCases(PER_KIND))]);
  const disclaimer = summary.ok ? summary.data.disclaimer : null;
  const kinds = cases.ok ? Object.values(cases.data.kinds) : [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-xs text-gray-500">
            <Link href="/" className="hover:underline">
              대시보드
            </Link>{" "}
            ›{" "}
            <Link href="/orgs" className="hover:underline">
              기관
            </Link>{" "}
            › 기관 발표로 확인된 사례
          </p>
          <h1 className="mt-1 text-2xl font-bold text-gray-900">기관 발표로 확인된 사례</h1>
        </div>
        {summary.ok && (
          <p className="text-xs text-gray-500">
            기관 발표 확인 사건 {summary.data.with_org_notice}건 · 기준일 {summary.data.as_of}
          </p>
        )}
      </div>

      <OrgTabs current="/orgs/notices" />

      <Panel
        title="뉴스에는 없었지만 기관은 발표한 사건"
        subtitle={`후속보도가 오래 없는 사건은 책임기관의 공식 보도자료·공지 게시판을 확인합니다. ${
          kinds.length > 0 ? `사례는 두 종류 — ${kinds.join(" / ")} — 로 종류별 최대 ${PER_KIND}건을 규칙이 고릅니다.` : ""
        } 발표 제목을 누르면 기관 글, 「기관 게시판으로」는 게시판, 「사건 근거 보기」는 근거 화면으로 갑니다. 기관 발표는 보조 근거이며 사건 상태를 바꾸지 않습니다.`}
        disclaimer={disclaimer}
      >
        {cases.ok ? <NoticeCases items={cases.data.items} /> : <ErrorBox title="사례를 불러오지 못했습니다" error={cases.error} />}
      </Panel>
    </div>
  );
}
