interface Props {
  values: number[];
  width?: number;
  height?: number;
  className?: string;
  title?: string;
}

/** Tiny inline-SVG sparkline of monthly article counts since the incident. */
export default function Sparkline({ values, width = 150, height = 36, className = "", title }: Props) {
  const padX = 3;
  const padY = 4;
  const n = values.length;
  const total = values.reduce((a, b) => a + b, 0);
  // Displayed max is the real one; the scale denominator is clamped so an all-zero series never divides by 0.
  const max = n > 0 ? Math.max(...values) : 0;
  const scaleMax = Math.max(1, max);

  if (n === 0) {
    return (
      <span className={`text-xs text-gray-400 ${className}`} title="보도량 데이터 없음">
        보도량 데이터 없음
      </span>
    );
  }

  if (max === 0) {
    return (
      <span className={`text-xs text-gray-400 ${className}`} title={`발생 이후 월별 보도량 (${n}개월, 보도 없음)`}>
        보도 없음
      </span>
    );
  }

  const stepX = n > 1 ? (width - padX * 2) / (n - 1) : 0;
  const px = (i: number) => padX + i * stepX;
  const py = (v: number) => height - padY - (v / scaleMax) * (height - padY * 2);
  const points = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`);
  const linePath = `M${points.join(" L")}`;
  const areaPath = `${linePath} L${px(n - 1).toFixed(1)},${height - padY} L${px(0).toFixed(1)},${height - padY} Z`;
  const lastIdx = n - 1;
  const label = title ?? `발생 이후 월별 보도량 (${n}개월, 총 ${total}건, 최대 ${max}건)`;

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
    >
      <title>{label}</title>
      <path d={areaPath} fill="#dbeafe" opacity={0.7} />
      <path d={linePath} fill="none" stroke="#2563eb" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={px(lastIdx)} cy={py(values[lastIdx])} r={2.2} fill="#1d4ed8" />
    </svg>
  );
}
