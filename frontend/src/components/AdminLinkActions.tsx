"use client";

import { useState } from "react";
import { describeError, postAdminLink } from "@/lib/api";
import type { AdminLinkAction, AdminLinkOut } from "@/lib/types";

interface Props {
  eventId: number;
  newsId: string;
}

type State =
  | { phase: "idle" }
  | { phase: "sending"; action: AdminLinkAction }
  | { phase: "done"; result: AdminLinkOut }
  | { phase: "error"; message: string; detail?: string };

/** 확정 / 해제 buttons -> POST /api/admin/links/{event_id}/{news_id}. */
export default function AdminLinkActions({ eventId, newsId }: Props) {
  const [state, setState] = useState<State>({ phase: "idle" });

  const run = async (action: AdminLinkAction) => {
    setState({ phase: "sending", action });
    try {
      const result = await postAdminLink(eventId, newsId, action);
      setState({ phase: "done", result });
    } catch (error) {
      const { message, detail } = describeError(error);
      setState({ phase: "error", message, detail });
    }
  };

  if (state.phase === "done") {
    return (
      <span className={`text-xs font-semibold ${state.result.confirmed ? "text-green-700" : "text-gray-500"}`} role="status">
        {state.result.confirmed ? `확정됨 (${state.result.link_method})` : "연결 해제됨"}
      </span>
    );
  }

  const sending = state.phase === "sending";
  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex gap-1.5">
        <button
          type="button"
          disabled={sending}
          onClick={() => void run("confirm")}
          className="rounded-md border border-green-300 bg-green-50 px-2.5 py-1 text-xs font-semibold text-green-800 hover:bg-green-100 disabled:opacity-50"
        >
          {sending && state.action === "confirm" ? "처리 중…" : "확정"}
        </button>
        <button
          type="button"
          disabled={sending}
          onClick={() => void run("reject")}
          className="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50"
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
