"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { TimeseriesRow } from "@/lib/dashboard";

/**
 * 월별/일별 시계열 — 발생 사건 수(막대) + 사건 이후 연결 기사 수(선; 후속보도로 확인된 기사만이 아님). 두 값 모두 '건' 이라 축은 하나다.
 * 범위 토글: 전체 / 2023~2025 / 최근 12개월(기준일 as_of 기준, 브라우저 시계와 무관).
 * 숫자는 전부 /api/dashboard/timeseries 에서 온다. 범위 밖 빈 구간만 0 으로 채운다.
 */

type Range = "all" | "y2325" | "last12";
type Interval = "month" | "day";

interface Props {
  month: TimeseriesRow[];
  day: TimeseriesRow[];
  asOf: string; // YYYY-MM-DD
}

const BAR = "#2a78d6"; // 발생 사건 (categorical slot 1)
const LINE = "#eb6834"; // 사건 이후 연결 기사 (categorical slot 2)
const GRID = "#e5e7eb";
const H = 260;
const PAD = { top: 16, right: 12, bottom: 28, left: 34 };

const RANGES: Array<{ key: Range; label: string }> = [
  { key: "all", label: "전체 기간" },
  { key: "y2325", label: "2023~2025" },
  { key: "last12", label: "최근 12개월" },
];

function emptyRow(bucket: string): TimeseriesRow {
  return { bucket, occurred: 0, followup_articles: 0, confirmed_followups: 0, dead: null, injured: null };
}

function monthBuckets(from: string, to: string): string[] {
  const out: string[] = [];
  let [y, m] = from.split("-").map(Number);
  const [ty, tm] = to.split("-").map(Number);
  while (y < ty || (y === ty && m <= tm)) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

function dayBuckets(from: string, to: string): string[] {
  const out: string[] = [];
  const d = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  while (d <= end) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

function shiftDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** 범위에 맞게 자르고, 데이터가 없는 구간은 0 행으로 채운다. */
function windowRows(rows: TimeseriesRow[], interval: Interval, range: Range, asOf: string): TimeseriesRow[] {
  if (range === "all") return rows;
  const byBucket = new Map(rows.map((r) => [r.bucket, r]));
  let buckets: string[];
  if (range === "y2325") {
    buckets = interval === "month" ? monthBuckets("2023-01", "2025-12") : dayBuckets("2023-01-01", "2025-12-31");
  } else if (interval === "month") {
    const [y, m] = asOf.split("-").map(Number);
    const start = m === 12 ? `${y}-01` : `${y - 1}-${String(m + 1).padStart(2, "0")}`;
    buckets = monthBuckets(start, asOf.slice(0, 7));
  } else {
    buckets = dayBuckets(shiftDays(asOf, -364), asOf);
  }
  return buckets.map((b) => byBucket.get(b) ?? emptyRow(b));
}

function niceMax(v: number): number {
  if (v <= 4) return 4;
  const pow = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * pow >= v) return m * pow;
  return 10 * pow;
}

function bucketLabel(bucket: string, interval: Interval): string {
  if (interval === "month") return `${bucket.slice(2, 4)}.${bucket.slice(5, 7)}`;
  return `${bucket.slice(5, 7)}.${bucket.slice(8, 10)}`;
}

function bucketLong(bucket: string, interval: Interval): string {
  const [y, m, d] = bucket.split("-").map(Number);
  return interval === "month" ? `${y}년 ${m}월` : `${y}년 ${m}월 ${d}일`;
}

/** 위쪽만 둥근(4px) 막대 경로 — 기준선 쪽은 각지게 */
function columnPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

export default function TimeseriesChart({ month, day, asOf }: Props) {
  const [interval, setIntervalKind] = useState<Interval>("month");
  const [range, setRange] = useState<Range>("all");
  const [hover, setHover] = useState<number | null>(null);
  const [width, setWidth] = useState(720);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const rows = useMemo(
    () => windowRows(interval === "month" ? month : day, interval, range, asOf),
    [month, day, interval, range, asOf],
  );
  const totals = useMemo(
    () => ({
      occurred: rows.reduce((s, r) => s + r.occurred, 0),
      followups: rows.reduce((s, r) => s + r.followup_articles, 0),
    }),
    [rows],
  );

  const innerW = Math.max(40, width - PAD.left - PAD.right);
  const innerH = H - PAD.top - PAD.bottom;
  const n = Math.max(1, rows.length);
  const slot = innerW / n;
  const barW = Math.max(1, Math.min(24, slot * 0.62));
  const yMax = niceMax(Math.max(1, ...rows.map((r) => Math.max(r.occurred, r.followup_articles))));
  const y = (v: number) => PAD.top + innerH - (v / yMax) * innerH;
  const cx = (i: number) => PAD.left + slot * i + slot / 2;
  const ticks = [0, yMax / 4, yMax / 2, (3 * yMax) / 4, yMax].filter((t) => Number.isInteger(t));
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(innerW / 52))));
  const linePts = rows.map((r, i) => `${cx(i).toFixed(1)},${y(r.followup_articles).toFixed(1)}`).join(" ");
  const showDots = slot >= 10;
  const hv = hover !== null ? rows[hover] : null;
  const isDay = interval === "day";
  const dayBarW = Math.max(1.5, Math.min(6, slot * 0.8));

  const btn = (active: boolean) =>
    `rounded-md px-2.5 py-1 text-xs font-semibold transition ${
      active ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"
    }`;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-gray-200 bg-white p-0.5" role="group" aria-label="집계 단위">
          <button type="button" className={btn(interval === "month")} aria-pressed={interval === "month"} onClick={() => setIntervalKind("month")}>
            월별
          </button>
          <button type="button" className={btn(interval === "day")} aria-pressed={interval === "day"} onClick={() => setIntervalKind("day")}>
            일별
          </button>
        </div>
        <div className="flex flex-wrap rounded-lg border border-gray-200 bg-white p-0.5" role="group" aria-label="기간">
          {RANGES.map((r) => (
            <button key={r.key} type="button" className={btn(range === r.key)} aria-pressed={range === r.key} onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-3 text-xs text-gray-600">
          <span className="flex items-center gap-1.5">
            <span className={`inline-block ${isDay ? "h-2.5 w-2.5 rounded-full" : "h-3 w-2.5 rounded-t-sm"}`} style={{ background: BAR }} aria-hidden />
            발생 사건 <b className="text-gray-900">{totals.occurred}</b>
          </span>
          <span className="flex items-center gap-1.5">
            <span className={`inline-block ${isDay ? "h-3 w-1" : "h-0.5 w-4 rounded"}`} style={{ background: LINE }} aria-hidden />
            사건 이후 연결 기사 <b className="text-gray-900">{totals.followups}</b>
          </span>
        </div>
      </div>

      <div ref={boxRef} className="relative w-full overflow-hidden" onMouseLeave={() => setHover(null)}>
        <svg width={width} height={H} role="img" aria-label={`${interval === "month" ? "월별" : "일별"} 발생 사건 수와 사건 이후 연결 기사 수`} className="block">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1} />
              <text x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="#6b7280">
                {t}
              </text>
            </g>
          ))}
          {hover !== null && <rect x={PAD.left + slot * hover} y={PAD.top} width={slot} height={innerH} fill="#f3f4f6" />}
          {isDay ? (
            <>
              {/* 일별: 사건 이후 연결 기사는 가는 막대, 발생 사건은 점 (수백 개 구간에서 선은 읽히지 않는다) */}
              {rows.map((r, i) =>
                r.followup_articles > 0 ? (
                  <rect key={r.bucket} x={cx(i) - dayBarW / 2} y={y(r.followup_articles)} width={dayBarW} height={y(0) - y(r.followup_articles)} fill={LINE} />
                ) : null,
              )}
              {rows.map((r, i) =>
                r.occurred > 0 ? <circle key={r.bucket} cx={cx(i)} cy={y(r.occurred)} r={4.5} fill={BAR} stroke="#fff" strokeWidth={2} /> : null,
              )}
            </>
          ) : (
            <>
              {rows.map((r, i) => (
                <path key={r.bucket} d={columnPath(cx(i) - barW / 2, y(r.occurred), barW, y(0) - y(r.occurred))} fill={BAR} />
              ))}
              <polyline points={linePts} fill="none" stroke={LINE} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {showDots &&
                rows.map((r, i) =>
                  r.followup_articles > 0 ? (
                    <circle key={r.bucket} cx={cx(i)} cy={y(r.followup_articles)} r={4} fill={LINE} stroke="#fff" strokeWidth={2} />
                  ) : null,
                )}
            </>
          )}
          {rows.map((r, i) =>
            i % labelEvery === 0 ? (
              <text key={r.bucket} x={cx(i)} y={H - 8} textAnchor="middle" fontSize={11} fill="#6b7280">
                {bucketLabel(r.bucket, interval)}
              </text>
            ) : null,
          )}
          <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} stroke="#9ca3af" strokeWidth={1} />
          {rows.map((r, i) => (
            <rect
              key={r.bucket}
              x={PAD.left + slot * i}
              y={PAD.top}
              width={slot}
              height={innerH}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onTouchStart={() => setHover(i)}
            />
          ))}
        </svg>

        {hv && hover !== null && (
          <div
            className="pointer-events-none absolute top-2 z-10 min-w-[150px] rounded-lg border border-gray-200 bg-white/95 px-3 py-2 text-xs shadow-lg"
            style={cx(hover) > width / 2 ? { right: width - cx(hover) + 10 } : { left: cx(hover) + 10 }}
          >
            <p className="mb-1 font-semibold text-gray-900">{bucketLong(hv.bucket, interval)}</p>
            <p className="flex items-center justify-between gap-3 text-gray-600">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-sm" style={{ background: BAR }} />
                발생 사건
              </span>
              <b className="text-gray-900">{hv.occurred}건</b>
            </p>
            <p className="flex items-center justify-between gap-3 text-gray-600">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-0.5 w-2.5" style={{ background: LINE }} />
                사건 이후 연결 기사
              </span>
              <b className="text-gray-900">{hv.followup_articles}건</b>
            </p>
            <p className="flex items-center justify-between gap-3 text-gray-500">
              <span>└ 조치 확인 보도</span>
              <b className="text-gray-800">{hv.confirmed_followups}건</b>
            </p>
            {(hv.dead !== null || hv.injured !== null) && (
              <p className="mt-1 border-t border-gray-100 pt-1 text-gray-600">
                보도된 인명 피해 사망 {hv.dead ?? "-"} · 부상 {hv.injured ?? "-"}
              </p>
            )}
          </div>
        )}
      </div>

      <details className="text-xs text-gray-500">
        <summary className="cursor-pointer select-none hover:text-gray-700">표로 보기</summary>
        <div className="mt-2 max-h-64 overflow-auto rounded-md border border-gray-200">
          <table className="w-full text-left tabular-nums">
            <thead className="sticky top-0 bg-gray-50 text-gray-600">
              <tr>
                <th className="px-2 py-1 font-medium">{interval === "month" ? "월" : "일"}</th>
                <th className="px-2 py-1 text-right font-medium">발생 사건</th>
                <th className="px-2 py-1 text-right font-medium">사건 이후 연결 기사</th>
                <th className="px-2 py-1 text-right font-medium">조치 확인 보도</th>
              </tr>
            </thead>
            <tbody>
              {rows
                .filter((r) => r.occurred || r.followup_articles)
                .map((r) => (
                  <tr key={r.bucket} className="border-t border-gray-100">
                    <td className="px-2 py-1">{r.bucket}</td>
                    <td className="px-2 py-1 text-right">{r.occurred}</td>
                    <td className="px-2 py-1 text-right">{r.followup_articles}</td>
                    <td className="px-2 py-1 text-right">{r.confirmed_followups}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
