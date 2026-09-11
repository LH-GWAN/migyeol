"use client";

import Link from "next/link";

/** Route-level error boundary: keeps the layout and shows a Korean message instead of a crash. */
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-900">
      <p className="font-semibold">페이지를 표시하는 중 문제가 발생했습니다.</p>
      <p className="mt-1 break-all font-mono text-xs text-red-700/80">{error.message}</p>
      {error.digest && <p className="mt-1 font-mono text-[11px] text-red-600/70">digest: {error.digest}</p>}
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={reset}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-gray-700"
        >
          다시 시도
        </button>
        <Link href="/" className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50">
          목록으로
        </Link>
      </div>
    </div>
  );
}
