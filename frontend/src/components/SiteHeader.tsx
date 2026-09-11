import Link from "next/link";
import { getMeta, safe, USE_MOCK } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

/** Service header: name, tagline, nav, data source + last refresh from GET /api/meta. */
export default async function SiteHeader() {
  const meta = await safe(getMeta());

  return (
    <header className="border-b border-gray-200 bg-white">
      <div className="mx-auto flex w-full max-w-[1100px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4">
        <div className="flex min-w-0 flex-col">
          <Link href="/" className="text-xl font-bold tracking-tight text-gray-900">
            미결<span className="ml-1 text-base font-medium text-gray-500">(未結)</span>
          </Link>
          <p className="text-xs text-gray-500">뉴스는 끝났지만, 문제도 끝났습니까?</p>
        </div>

        <nav aria-label="주 메뉴" className="flex items-center gap-1 text-sm font-medium">
          <Link href="/" className="rounded-md px-3 py-1.5 text-gray-700 hover:bg-gray-100">
            목록
          </Link>
          <Link href="/admin" className="rounded-md px-3 py-1.5 text-gray-700 hover:bg-gray-100">
            관리자
          </Link>
        </nav>

        <div className="ml-auto text-right text-xs leading-relaxed text-gray-500">
          {meta.ok ? (
            <>
              <p>
                데이터: 빅카인즈 API{" "}
                <span
                  className={`rounded px-1.5 py-0.5 font-mono font-semibold ${
                    meta.data.bigkinds_mode === "live" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
                  }`}
                >
                  {meta.data.bigkinds_mode}
                </span>
                {USE_MOCK && <span className="ml-1 text-gray-400">(프런트 목 데이터)</span>}
              </p>
              <p>
                마지막 갱신{" "}
                <time dateTime={meta.data.last_pipeline_run?.finished_at ?? undefined}>
                  {meta.data.last_pipeline_run ? formatDateTime(meta.data.last_pipeline_run.finished_at) : "기록 없음"}
                </time>
                {meta.data.last_pipeline_run && !meta.data.last_pipeline_run.ok && (
                  <span className="ml-1 text-red-600">(실패)</span>
                )}
                <span className="ml-2 text-gray-400">· 사건 {meta.data.event_count}건</span>
              </p>
            </>
          ) : (
            <>
              <p>
                데이터: 빅카인즈 API <span className="rounded bg-red-100 px-1.5 py-0.5 font-semibold text-red-700">연결 안 됨</span>
              </p>
              <p>마지막 갱신 -</p>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
