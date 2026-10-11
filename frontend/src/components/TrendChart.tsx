import { formatDate, formatYearMonth } from "@/lib/format";
import type { DeadlinePrecision, TrendOut } from "@/lib/types";

interface Props {
  trend: TrendOut;
}

interface Marker {
  key: "occurred_at" | "deadline_date" | "today";
  label: string;
  date: string;
  precision: DeadlinePrecision;
  x: number;
  color: string;
  dash?: string;
}

const W = 760;
const H = 250;
const PAD = { top: 34, right: 20, bottom: 40, left: 44 };

function daysInMonth(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/**
 * Where an ISO date falls relative to the monthly band layout.
 * `labels` is a contiguous month range (YYYYMM), so a date is either inside it, before it, or after it.
 */
function locate(date: string, labels: string[], x0: number, band: number): { x: number } | "before" | "after" | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!m || labels.length === 0) return null;
  const ym = `${m[1]}${m[2]}`;
  if (ym < labels[0]) return "before";
  if (ym > labels[labels.length - 1]) return "after";
  const idx = labels.indexOf(ym);
  if (idx === -1) return null; // non-contiguous series (not expected from the backend)
  const day = Number(m[3]) || 1;
  const dim = daysInMonth(Number(m[1]), Number(m[2]));
  return { x: x0 + idx * band + ((day - 1) / dim) * band };
}

/** Monthly bar chart with vertical markers for 발생일 / 약속 기한 / 오늘. */
export default function TrendChart({ trend }: Props) {
  const series = trend.series ?? [];
  if (series.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">
        월별 보도량 데이터가 없습니다.
      </div>
    );
  }

  const labels = series.map((p) => p.label);
  const n = series.length;
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const band = plotW / n;
  const barW = Math.max(4, Math.min(band * 0.62, 40));
  // Displayed max is the real one; the scale denominator is clamped so an all-zero series still lays out.
  const maxHits = Math.max(...series.map((p) => p.hits));
  const scaleMax = Math.max(1, maxHits);
  const noReports = maxHits === 0;
  const yOf = (v: number) => PAD.top + plotH - (v / scaleMax) * plotH;
  const ticks = noReports ? [0] : Array.from(new Set([0, Math.round(maxHits / 2), maxHits]));
  const labelEvery = n <= 12 ? 1 : Math.ceil(n / 12);

  const { occurred_at, deadline_date, deadline_precision, today } = trend.markers;
  const deadlinePrecision: DeadlinePrecision = deadline_precision ?? "day";

  const markers: Marker[] = [];
  let deadlineOutside: "before" | "after" | null = null;

  if (occurred_at) {
    const loc = locate(occurred_at, labels, PAD.left, band);
    // The series starts at the incident month, so "before" only happens with a trimmed series: clamp to the left edge.
    const x = loc === "before" ? PAD.left : loc !== null && loc !== "after" ? loc.x : null;
    if (x !== null) markers.push({ key: "occurred_at", label: "발생", date: occurred_at, precision: "day", color: "#111827", x });
  }
  if (deadline_date && deadlinePrecision !== "none") {
    const loc = locate(deadline_date, labels, PAD.left, band);
    if (loc === "before" || loc === "after") {
      // Outside the series: never fake a position at the chart edge; say so in the legend instead.
      deadlineOutside = loc;
    } else if (loc !== null) {
      markers.push({
        key: "deadline_date",
        label: "약속 기한",
        date: deadline_date,
        precision: deadlinePrecision,
        color: "#ea580c",
        dash: "5 4",
        x: loc.x,
      });
    }
  }
  if (today) {
    const loc = locate(today, labels, PAD.left, band);
    // "today" after the last month means the series simply has not caught up yet: pin it to the right edge.
    const x = loc === "after" ? PAD.left + plotW : loc !== null && loc !== "before" ? loc.x : null;
    if (x !== null) markers.push({ key: "today", label: "오늘", date: today, precision: "day", color: "#2563eb", dash: "2 3", x });
  }
  markers.sort((a, b) => a.x - b.x);

  // Alternate label heights when two markers sit close together.
  const labelY: number[] = [];
  markers.forEach((m, i) => {
    const prev = i > 0 ? markers[i - 1] : null;
    labelY.push(prev && m.x - prev.x < 60 && labelY[i - 1] === 14 ? 26 : 14);
  });

  const rangeText = `${formatYearMonth(labels[0])} ~ ${formatYearMonth(labels[n - 1])}`;

  return (
    <figure>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full min-w-[520px]"
          role="img"
          aria-label={`월별 보도량 추이 (${rangeText}, ${noReports ? "보도 없음" : `최대 ${maxHits}건`})`}
        >
          <title>월별 보도량 추이</title>
          {/* y grid + ticks */}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={W - PAD.right} y1={yOf(t)} y2={yOf(t)} stroke="#e5e7eb" strokeWidth={1} />
              <text x={PAD.left - 8} y={yOf(t) + 4} textAnchor="end" fontSize={11} fill="#6b7280">
                {t}
              </text>
            </g>
          ))}
          {noReports && (
            <text x={PAD.left + plotW / 2} y={PAD.top + plotH / 2} textAnchor="middle" fontSize={13} fill="#9ca3af">
              보도 없음
            </text>
          )}
          {/* bars */}
          {series.map((p, i) => {
            const x = PAD.left + i * band + (band - barW) / 2;
            const y = yOf(p.hits);
            const h = PAD.top + plotH - y;
            return (
              <g key={p.label}>
                <rect x={x} y={y} width={barW} height={Math.max(h, p.hits > 0 ? 1 : 0)} rx={2} fill="#93c5fd">
                  <title>{`${formatYearMonth(p.label)}: ${p.hits}건`}</title>
                </rect>
                {i % labelEvery === 0 && (
                  <text
                    x={PAD.left + i * band + band / 2}
                    y={H - PAD.bottom + 16}
                    textAnchor="middle"
                    fontSize={11}
                    fill="#6b7280"
                  >
                    {formatYearMonth(p.label, "short")}
                  </text>
                )}
              </g>
            );
          })}
          {/* baseline */}
          <line x1={PAD.left} x2={W - PAD.right} y1={PAD.top + plotH} y2={PAD.top + plotH} stroke="#9ca3af" />
          {/* vertical markers */}
          {markers.map((m, i) => (
            <g key={m.key}>
              <line
                x1={m.x}
                x2={m.x}
                y1={PAD.top - 4}
                y2={PAD.top + plotH}
                stroke={m.color}
                strokeWidth={1.5}
                strokeDasharray={m.dash}
              />
              <text
                x={m.x}
                y={labelY[i]}
                textAnchor="middle"
                fontSize={11}
                fontWeight={600}
                fill={m.color}
              >
                {m.label}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-600">
        {markers.map((m) => (
          <span key={m.key} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-3 w-0 border-l-2"
              style={{ borderColor: m.color, borderStyle: m.dash ? "dashed" : "solid" }}
            />
            {m.label} {formatDate(m.date, m.precision)}
          </span>
        ))}
        {deadlineOutside && deadline_date && (
          <span className="inline-flex items-center gap-1.5 text-orange-700">
            <span aria-hidden className="inline-block h-3 w-0 border-l-2 border-dashed border-orange-600" />
            약속 기한 → {formatDate(deadline_date, deadlinePrecision === "day" ? "month" : deadlinePrecision)} (차트 범위 밖)
          </span>
        )}
        <span className="ml-auto text-gray-500">
          {noReports
            ? "보도 없음"
            : `최고 ${trend.peak_hits}건${trend.peak_label ? ` (${formatYearMonth(trend.peak_label)})` : ""} · 최근 평균 ${trend.recent_mean}건 · 감소율 ${Math.round(trend.drop_ratio * 100)}%`}
        </span>
      </figcaption>
    </figure>
  );
}
