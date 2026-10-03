"use client";

import { useState } from "react";
import { describeError, resolveReport } from "@/lib/api";

interface Props {
  reportId: number;
}

type State =
  | { phase: "idle" }
  | { phase: "sending" }
  | { phase: "done" }
  | { phase: "error"; message: string; detail?: string };

/** 미처리 신고 -> 처리 완료 버튼. POST /api/admin/reports/{report_id}/resolve. */
export default function ReportResolveButton({ reportId }: Props) {
  const [state, setState] = useState<State>({ phase: "idle" });

  const run = async () => {
    setState({ phase: "sending" });
    try {
      await resolveReport(reportId);
      setState({ phase: "done" });
    } catch (error) {
      const { message, detail } = describeError(error);
      setState({ phase: "error", message, detail });
    }
  };

  if (state.phase === "done") {
    return (
      <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-500" role="status">
        처리됨
      </span>
    );
  }

  const sending = state.phase === "sending";
  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex items-center gap-1.5">
        <span className="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800">미처리</span>
        <button
          type="button"
          disabled={sending}
          onClick={() => void run()}
          className="whitespace-nowrap rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50"
        >
          {sending ? "처리 중…" : "처리 완료"}
        </button>
      </div>
      {state.phase === "error" && (
        <span className="text-xs text-red-700" role="alert">
          실패: {state.message}
          {state.detail ? ` (${state.detail})` : ""}
        </span>
      )}
    </div>
  );
}
