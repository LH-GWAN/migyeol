import type { Metadata } from "next";
import Link from "next/link";
import ErrorBox from "@/components/ErrorBox";
import Panel from "@/components/dashboard/Panel";
import RecentFeed from "@/components/dashboard/RecentFeed";
import { safe } from "@/lib/api";
import { getDashboardSummary, getRecentFollowups } from "@/lib/dashboard";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "최근 후속보도" };

const LIMIT = 50;

/** 대시보드에서 분리한 최근 후속보도 전체 목록 (2026-10-27 피드백: 대시보드에는 5건만). */
export default async function FollowupsPage() {
  const [summary, recent] = await Promise.all([safe(getDashboardSummary()), safe(getRecentFollowups(LIMIT))]);
  const disclaimer = summary.ok ? summary.data.disclaimer : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-xs text-gray-500">
            <Link href="/" className="hover:underline">
              대시보드
            </Link>{" "}
            › 최근 후속보도
          </p>
          <h1 className="mt-1 text-2xl font-bold text-gray-900">최근 후속보도</h1>
        </div>
        {summary.ok && (
          <p className="text-xs text-gray-500">
            후속보도 확인 사건 {summary.data.with_followup}건 · 기준일 {summary.data.as_of}
          </p>
        )}
      </div>
      <Panel
        title="조치 완료·진행 또는 새로운 문제를 전한 기사"
        subtitle={`최신 ${LIMIT}건까지. 기사 제목은 언론사 페이지(새 창)로, 사건명은 타임라인으로 이어집니다.`}
        disclaimer={disclaimer}
      >
        {recent.ok ? <RecentFeed items={recent.data} /> : <ErrorBox title="최근 후속보도를 불러오지 못했습니다" error={recent.error} />}
      </Panel>
    </div>
  );
}
