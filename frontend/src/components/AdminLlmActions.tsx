"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { describeError, llmAck, llmRetry } from "@/lib/api";

interface Props {
  eventId: number;
  acked: boolean;
}

type State = { phase: "idle" } | { phase: "sending" } | { phase: "done"; label: string } | { phase: "error"; message: string };

/** 2회 실패한 LLM 항목: 「재시도」(카운터 초기화, 다음 실행에서 다시 호출) / 「확인함」(검토 큐에서 내림). */
export default function AdminLlmActions({ eventId, acked }: Props) {
  const router = useRouter();
  const [state, setState] = useState<State>({ phase: "idle" });
  const [refreshing, startTransition] = useTransition();

  const run = async (kind: "retry" | "ack") => {
    setState({ phase: "sending" });
    try {
      const out = kind === "retry" ? await llmRetry(eventId) : await llmAck(eventId);
      setState({ phase: "done", label: kind === "retry" ? `재시도 예약됨 (${out.removed ?? 0}건 초기화)` : "확인함" });
      startTransition(() => router.refresh());
    } catch (error) {
      setState({ phase: "error", message: describeError(error).message });
    }
  };

  if (state.phase === "done") {
    return (
      <span className="text-xs text-gray-600" role="status">
        {state.label}
        {refreshing ? " (갱신 중…)" : ""}
      </span>
    );
  }
  const sending = state.phase === "sending";
  const btn = "rounded-md border border-gray-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50";
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <span className="text-[11px] text-amber-800">LLM 2회 실패 항목{acked ? " (확인됨)" : ""}</span>
      <button type="button" className={btn} disabled={sending} onClick={() => void run("retry")}>
        재시도
      </button>
      {!acked && (
        <button type="button" className={btn} disabled={sending} onClick={() => void run("ack")}>
          확인함
        </button>
      )}
      {state.phase === "error" && <span className="text-[11px] text-red-700">실패: {state.message}</span>}
    </div>
  );
}
