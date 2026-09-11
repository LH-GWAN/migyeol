"use client";

import { useState } from "react";
import { describeError, postReport } from "@/lib/api";
import type { ReportOut, ReportType } from "@/lib/types";

interface Props {
  eventId: number;
  type: ReportType;
  newsId?: string;
  label: string;
  /** Short helper text shown above the comment box. */
  description?: string;
  size?: "sm" | "md";
}

type State =
  | { phase: "idle" }
  | { phase: "editing" }
  | { phase: "sending" }
  | { phase: "done"; result: ReportOut }
  | { phase: "error"; message: string; detail?: string };

/**
 * Inline report button -> optional comment -> POST /api/events/{id}/reports.
 * Used for "잘못된 연결 신고" (wrong_link + news_id) and "상태 이의 제기" (wrong_status).
 */
export default function ReportForm({ eventId, type, newsId, label, description, size = "md" }: Props) {
  const [state, setState] = useState<State>({ phase: "idle" });
  const [comment, setComment] = useState("");

  const buttonClass =
    size === "sm"
      ? "rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
      : "rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-800 hover:bg-gray-50";

  if (state.phase === "done") {
    return (
      <p className="inline-flex items-center gap-1 text-xs text-green-700" role="status">
        <span aria-hidden>✓</span> 접수되었습니다 (신고 #{state.result.id})
      </p>
    );
  }

  if (state.phase === "idle") {
    return (
      <button type="button" className={buttonClass} onClick={() => setState({ phase: "editing" })}>
        {label}
      </button>
    );
  }

  const submit = async () => {
    setState({ phase: "sending" });
    try {
      const trimmed = comment.trim();
      const result = await postReport(eventId, {
        type,
        ...(newsId ? { news_id: newsId } : {}),
        ...(trimmed ? { comment: trimmed } : {}),
      });
      setState({ phase: "done", result });
    } catch (error) {
      const { message, detail } = describeError(error);
      setState({ phase: "error", message, detail });
    }
  };

  const sending = state.phase === "sending";

  return (
    <form
      className="w-full max-w-md rounded-md border border-gray-200 bg-gray-50 p-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="font-semibold text-gray-800">{label}</p>
      {description && <p className="mt-0.5 text-xs text-gray-500">{description}</p>}
      {newsId && <p className="mt-0.5 font-mono text-[11px] text-gray-400">news_id: {newsId}</p>}
      <textarea
        className="mt-2 w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
        rows={3}
        placeholder="의견(선택)"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        disabled={sending}
        maxLength={1000}
      />
      {state.phase === "error" && (
        <p className="mt-1 text-xs text-red-700" role="alert">
          전송 실패: {state.message}
          {state.detail ? <span className="ml-1 font-mono text-red-600/80">({state.detail})</span> : null}
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="submit"
          disabled={sending}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-gray-700 disabled:opacity-50"
        >
          {sending ? "전송 중…" : "보내기"}
        </button>
        <button
          type="button"
          disabled={sending}
          onClick={() => setState({ phase: "idle" })}
          className="rounded-md px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-100 disabled:opacity-50"
        >
          취소
        </button>
      </div>
    </form>
  );
}
