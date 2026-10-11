"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { describeError, postAdminLink } from "@/lib/api";
import type { AdminLinkAction, AdminLinkOut } from "@/lib/types";

interface Props {
  eventId: number;
  newsId: string;
  /** "pending": 확정 / 해제 for a low-confidence link. "rejected": 되돌리기 for an already rejected link. */
  mode?: "pending" | "rejected";
}

type State =
  | { phase: "idle" }
  | { phase: "confirm-reject" }
  | { phase: "sending"; action: AdminLinkAction }
  | { phase: "done"; result: AdminLinkOut }
  | { phase: "error"; message: string; detail?: string };

const REJECT_WARNING = "해제하면 이 사건에서 제외되며 자동 재연결되지 않습니다(관리자 화면에서 되돌릴 수 있음)";

function doneLabel(result: AdminLinkOut): string {
  if (result.confirmed) return `확정됨 (${result.link_method})`;
  if (result.rejected) return "연결 해제됨";
  return "연결 복구됨";
}

/**
 * 확정 / 해제 / 되돌리기 -> POST /api/admin/links/{event_id}/{news_id}.
 * 해제 needs an explicit second click; every action refreshes the server-rendered queue afterwards.
 */
export default function AdminLinkActions({ eventId, newsId, mode = "pending" }: Props) {
  const router = useRouter();
  const [state, setState] = useState<State>({ phase: "idle" });
  const [refreshing, startTransition] = useTransition();

  const run = async (action: AdminLinkAction) => {
    setState({ phase: "sending", action });
    try {
      const result = await postAdminLink(eventId, newsId, action);
      setState({ phase: "done", result });
      startTransition(() => router.refresh());
    } catch (error) {
      const { message, detail } = describeError(error);
      setState({ phase: "error", message, detail });
    }
  };

  if (state.phase === "done") {
    return (
      <span
        className={`text-xs font-semibold ${state.result.confirmed ? "text-green-700" : state.result.rejected ? "text-gray-500" : "text-blue-700"}`}
        role="status"
      >
        {doneLabel(state.result)}
        {refreshing && <span className="ml-1 font-normal text-gray-400">(목록 갱신 중…)</span>}
      </span>
    );
  }

  const sending = state.phase === "sending";
  const btnBase = "rounded-md border px-2.5 py-1 text-xs font-semibold disabled:opacity-50";

  if (mode === "rejected") {
    return (
      <div className="flex flex-col items-start gap-1">
        <button
          type="button"
          disabled={sending}
          onClick={() => void run("restore")}
          className={`${btnBase} border-blue-300 bg-blue-50 text-blue-800 hover:bg-blue-100`}
        >
          {sending ? "처리 중…" : "되돌리기"}
        </button>
        {state.phase === "error" && (
          <span className="text-xs text-red-700" role="alert">
            실패: {state.message}
            {state.detail ? ` (${state.detail})` : ""}
          </span>
        )}
      </div>
    );
  }

  if (state.phase === "confirm-reject") {
    return (
      <div className="flex max-w-[280px] flex-col items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 p-2">
        <p className="text-xs leading-relaxed text-amber-900">{REJECT_WARNING}</p>
        <div className="flex gap-1.5">
          <button
            type="button"
            onClick={() => void run("reject")}
            className={`${btnBase} border-red-300 bg-red-50 text-red-800 hover:bg-red-100`}
          >
            해제 확정
          </button>
          <button
            type="button"
            onClick={() => setState({ phase: "idle" })}
            className={`${btnBase} border-gray-300 bg-white text-gray-700 hover:bg-gray-100`}
          >
            취소
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex gap-1.5">
        <button
          type="button"
          disabled={sending}
          onClick={() => void run("confirm")}
          className={`${btnBase} border-green-300 bg-green-50 text-green-800 hover:bg-green-100`}
        >
          {sending && state.action === "confirm" ? "처리 중…" : "확정"}
        </button>
        <button
          type="button"
          disabled={sending}
          onClick={() => setState({ phase: "confirm-reject" })}
          className={`${btnBase} border-gray-300 bg-white text-gray-700 hover:bg-gray-100`}
        >
          {sending && state.action === "reject" ? "처리 중…" : "해제"}
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
