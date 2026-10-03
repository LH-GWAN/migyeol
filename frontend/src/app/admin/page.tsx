import type { Metadata } from "next";
import Link from "next/link";
import AdminLinkActions from "@/components/AdminLinkActions";
import { ArticleLink, ArticleMeta } from "@/components/ArticleItem";
import Disclaimer from "@/components/Disclaimer";
import ErrorBox from "@/components/ErrorBox";
import ReportResolveButton from "@/components/ReportResolveButton";
import StatusBadge from "@/components/StatusBadge";
import { getReviewQueue, safe } from "@/lib/api";
import { formatDate, formatDateTime, formatDaysAgo, formatPercent, formatScore, REPORT_TYPE_LABELS } from "@/lib/format";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "관리자 검토 큐" };

export default async function AdminPage() {
  const result = await safe(getReviewQueue());

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">관리자 검토 큐</h1>
        <p className="mt-1 text-sm text-gray-600">검토가 필요한 사건, 저신뢰 연결, 사용자 신고를 한곳에서 처리합니다.</p>
      </div>

      {!result.ok ? (
        <ErrorBox title="검토 큐를 불러오지 못했습니다" error={result.error} />
      ) : (
        <>
          {/* 1. Events needing review */}
          <section className="rounded-lg border border-gray-200 bg-white p-5">
            <h2 className="text-base font-bold text-gray-900">
              검토 필요 사건 <span className="text-sm font-normal text-gray-500">{result.data.events.length}건</span>
            </h2>
            {result.data.events.length === 0 ? (
              <p className="mt-3 text-sm text-gray-500">검토가 필요한 사건이 없습니다.</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[720px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 text-left text-xs text-gray-500">
                      <th className="py-2 pr-3 font-semibold">사건</th>
                      <th className="py-2 pr-3 font-semibold">현재 상태</th>
                      <th className="py-2 pr-3 font-semibold">신뢰도</th>
                      <th className="py-2 pr-3 font-semibold">우선순위</th>
                      <th className="py-2 pr-3 font-semibold">기한</th>
                      <th className="py-2 pr-3 font-semibold">최근 후속</th>
                      <th className="py-2 font-semibold">보기</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.data.events.map((c) => (
                      <tr key={c.id} className="border-b border-gray-100 align-top">
                        <td className="py-3 pr-3">
                          <Link href={`/events/${c.id}`} className="font-medium text-gray-900 hover:underline">
                            {c.title}
                          </Link>
                          <p className="text-xs text-gray-500">
                            #{c.id} · {c.region} · {c.incident_type}
                          </p>
                        </td>
                        <td className="py-3 pr-3">
                          <StatusBadge status={c.status} size="sm" />
                        </td>
                        <td className="py-3 pr-3">{formatPercent(c.status_confidence)}</td>
                        <td className="py-3 pr-3">{c.priority_score.toFixed(1)}</td>
                        <td className="py-3 pr-3">
                          {c.deadline_date ? formatDate(c.deadline_date, c.primary_promise?.deadline_precision ?? "day") : "-"}
                          {c.deadline_passed_days !== null && (
                            <span className="ml-1 text-xs font-semibold text-red-700">경과 {c.deadline_passed_days}일</span>
                          )}
                        </td>
                        <td className="py-3 pr-3">{formatDaysAgo(c.days_since_last_followup)}</td>
                        <td className="py-3 text-xs">
                          <Link href={`/events/${c.id}/evidence`} className="text-blue-700 hover:underline">
                            근거
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Disclaimer className="mt-3" />
          </section>

          {/* 2. Low-confidence links */}
          <section className="rounded-lg border border-gray-200 bg-white p-5">
            <h2 className="text-base font-bold text-gray-900">
              저신뢰 연결 <span className="text-sm font-normal text-gray-500">{result.data.links.length}건</span>
            </h2>
            <p className="mt-1 text-xs text-gray-500">
              확정하면 수동 연결(manual)로 기록되고, 해제하면 연결이 삭제됩니다.
            </p>
            {result.data.links.length === 0 ? (
              <p className="mt-3 text-sm text-gray-500">검토할 저신뢰 연결이 없습니다.</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 text-left text-xs text-gray-500">
                      <th className="py-2 pr-3 font-semibold">사건</th>
                      <th className="py-2 pr-3 font-semibold">기사</th>
                      <th className="py-2 pr-3 font-semibold">점수</th>
                      <th className="py-2 pr-3 font-semibold">통과한 규칙</th>
                      <th className="py-2 font-semibold">처리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.data.links.map((l) => (
                      <tr key={`${l.event_id}-${l.article.news_id}`} className="border-b border-gray-100 align-top">
                        <td className="py-3 pr-3">
                          <Link href={`/events/${l.event_id}/evidence`} className="text-gray-900 hover:underline">
                            {l.event_title}
                          </Link>
                          <p className="text-xs text-gray-400">#{l.event_id}</p>
                        </td>
                        <td className="py-3 pr-3">
                          <ArticleLink article={l.article} className="text-sm" />
                          <div className="mt-0.5">
                            <ArticleMeta article={l.article} />
                          </div>
                          <p className="mt-0.5 font-mono text-[11px] text-gray-400">{l.article.news_id}</p>
                        </td>
                        <td className="py-3 pr-3 font-semibold">{formatScore(l.link_score)}</td>
                        <td className="py-3 pr-3 font-mono text-xs text-gray-600">{l.link_reason || "-"}</td>
                        <td className="py-3">
                          <AdminLinkActions eventId={l.event_id} newsId={l.article.news_id} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 3. Reports */}
          <section className="rounded-lg border border-gray-200 bg-white p-5">
            <h2 className="text-base font-bold text-gray-900">
              신고 목록 <span className="text-sm font-normal text-gray-500">{result.data.reports.length}건</span>
            </h2>
            {result.data.reports.length === 0 ? (
              <p className="mt-3 text-sm text-gray-500">접수된 신고가 없습니다.</p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 text-left text-xs text-gray-500">
                      <th className="py-2 pr-3 font-semibold">#</th>
                      <th className="py-2 pr-3 font-semibold">사건</th>
                      <th className="py-2 pr-3 font-semibold">유형</th>
                      <th className="py-2 pr-3 font-semibold">기사 ID</th>
                      <th className="py-2 pr-3 font-semibold">의견</th>
                      <th className="py-2 pr-3 font-semibold">접수 시각</th>
                      <th className="py-2 font-semibold">처리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.data.reports.map((r) => (
                      <tr key={r.id} className={`border-b border-gray-100 align-top ${r.resolved ? "text-gray-400" : ""}`}>
                        <td className="py-3 pr-3">{r.id}</td>
                        <td className="py-3 pr-3">
                          <Link href={`/events/${r.event_id}/evidence`} className="hover:underline">
                            {r.event_title}
                          </Link>
                        </td>
                        <td className="py-3 pr-3">
                          {REPORT_TYPE_LABELS[r.type] ?? r.type}
                          <span className="ml-1 font-mono text-[11px] text-gray-400">({r.type})</span>
                        </td>
                        <td className="py-3 pr-3 font-mono text-[11px]">{r.news_id ?? "-"}</td>
                        <td className="py-3 pr-3 text-xs leading-relaxed">{r.comment ?? "-"}</td>
                        <td className="py-3 pr-3 text-xs">{formatDateTime(r.created_at)}</td>
                        <td className="py-3 text-xs">
                          {r.resolved ? (
                            <span className="rounded bg-gray-100 px-1.5 py-0.5">처리됨</span>
                          ) : (
                            <ReportResolveButton reportId={r.id} />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
