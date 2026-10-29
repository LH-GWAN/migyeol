"use client";

import { useState, type ReactNode } from "react";

/**
 * 긴 목록을 처음 N건만 보이고 「더보기 (M건)」로 펼치는 클라이언트 토글 (2026-10-29 피드백: 타임라인·근거 화면이 너무 길다).
 * 항목은 서버 컴포넌트가 그려서 넘긴다(숫자·API 는 그대로, 표시만 줄인다). 페이지 이동 없이 펼치고 「접기」로 되돌린다.
 */

interface ToggleProps {
  hidden: number;
  open: boolean;
  onToggle: () => void;
  moreLabel: string;
  className?: string;
}

function ToggleButton({ hidden, open, onToggle, moreLabel, className = "" }: ToggleProps) {
  if (hidden <= 0) return null;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className={`mt-2 inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-3 py-1 text-xs font-semibold text-blue-700 hover:bg-gray-50 ${className}`}
    >
      {open ? "접기" : `${moreLabel} (${hidden}건)`}
    </button>
  );
}

interface ListProps {
  /** `<li>` 요소 배열 (key 포함) */
  items: ReactNode[];
  /** 처음에 보이는 건수 */
  initial?: number;
  className?: string;
  moreLabel?: string;
}

/** `<ul>` 목록 — 처음 `initial`건만, 나머지는 버튼으로 펼친다. 숨길 것이 없으면 버튼이 없다. */
export function ShowMoreList({ items, initial = 3, className = "divide-y divide-gray-100", moreLabel = "더보기" }: ListProps) {
  const [open, setOpen] = useState(false);
  const hidden = Math.max(0, items.length - initial);
  const shown = open || hidden === 0 ? items : items.slice(0, initial);
  return (
    <>
      <ul className={className}>{shown}</ul>
      <ToggleButton hidden={hidden} open={open} onToggle={() => setOpen((v) => !v)} moreLabel={moreLabel} />
    </>
  );
}

interface TableProps {
  /** `<thead>` 요소 */
  head: ReactNode;
  /** `<tr>` 요소 배열 (key 포함) */
  rows: ReactNode[];
  initial?: number;
  tableClassName?: string;
  moreLabel?: string;
}

/** 표 — 처음 `initial`행만, 나머지는 버튼으로 펼친다. 가로로 넓은 표는 감싸는 요소가 스크롤된다. */
export function ShowMoreTable({ head, rows, initial = 3, tableClassName = "w-full border-collapse text-sm", moreLabel = "더보기" }: TableProps) {
  const [open, setOpen] = useState(false);
  const hidden = Math.max(0, rows.length - initial);
  const shown = open || hidden === 0 ? rows : rows.slice(0, initial);
  return (
    <>
      <div className="overflow-x-auto">
        <table className={tableClassName}>
          {head}
          <tbody>{shown}</tbody>
        </table>
      </div>
      <ToggleButton hidden={hidden} open={open} onToggle={() => setOpen((v) => !v)} moreLabel={moreLabel} />
    </>
  );
}
