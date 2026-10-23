"use client";

import { useState } from "react";

interface Props {
  markdown: string;
  fileName: string;
}

/** 초안 복사 / Markdown 다운로드 버튼. 클립보드 API 가 막힌 환경(비 HTTPS 등)은 textarea 선택 복사로 대신한다. */
export default function DraftActions({ markdown, fileName }: Props) {
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);

  function copyViaTextarea(): boolean {
    const ta = document.createElement("textarea");
    ta.value = markdown;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    document.body.removeChild(ta);
    return ok;
  }

  async function copy() {
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(markdown);
        ok = true;
      }
    } catch {
      ok = false; // 권한이 막힌 환경(임베드 창 등)은 아래 방식으로
    }
    if (!ok) ok = copyViaTextarea();
    setCopied(ok ? "ok" : "fail");
    window.setTimeout(() => setCopied(null), 2500);
  }

  function download() {
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={copy}
        className="inline-flex items-center gap-1.5 rounded-md bg-gray-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-gray-700"
      >
        {copied === "ok" ? "복사했습니다" : "초안 복사"}
      </button>
      <button
        type="button"
        onClick={download}
        className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-800 hover:bg-gray-50"
      >
        Markdown 다운로드
      </button>
      <span role="status" aria-live="polite" className="text-xs text-gray-500">
        {copied === "fail" ? "복사하지 못했습니다. 다운로드를 이용해 주세요." : ""}
      </span>
    </div>
  );
}
