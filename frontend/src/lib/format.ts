import type { DeadlinePrecision, Phase, ReportType, SignalKind, Strength } from "./types";

/** Split "YYYY-MM-DD..." without touching the Date object (avoids timezone shifts). */
function parseYmd(iso: string): { y: number; m: number; d: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return null;
  return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) };
}

/**
 * 2026-10-31 + day   -> "2026년 10월 31일"
 * 2026-10-31 + month -> "2026년 10월"
 * 2026-10-31 + year  -> "2026년"
 */
export function formatDate(
  iso: string | null | undefined,
  precision: DeadlinePrecision = "day",
  fallback = "-",
): string {
  if (!iso) return fallback;
  const p = parseYmd(iso);
  if (!p) return iso;
  if (precision === "year") return `${p.y}년`;
  if (precision === "month") return `${p.y}년 ${p.m}월`;
  return `${p.y}년 ${p.m}월 ${p.d}일`;
}

/** "2026-09-11T04:00:12+09:00" -> "2026년 9월 11일 04:00" (rendered as given, KST per contract). */
export function formatDateTime(iso: string | null | undefined, fallback = "-"): string {
  if (!iso) return fallback;
  const p = parseYmd(iso);
  if (!p) return iso;
  const time = /T(\d{2}):(\d{2})/.exec(iso);
  const hhmm = time ? ` ${time[1]}:${time[2]}` : "";
  return `${p.y}년 ${p.m}월 ${p.d}일${hhmm}`;
}

/** 32 -> "32일 전", 0 -> "오늘", null -> fallback */
export function formatDaysAgo(days: number | null | undefined, fallback = "없음"): string {
  if (days === null || days === undefined || Number.isNaN(days)) return fallback;
  if (days <= 0) return "오늘";
  return `${days}일 전`;
}

/** "202507" -> "2025년 7월" ; short -> "25.07" */
export function formatYearMonth(label: string, style: "long" | "short" = "long"): string {
  const match = /^(\d{4})(\d{2})$/.exec(label);
  if (!match) return label;
  const y = Number(match[1]);
  const m = Number(match[2]);
  if (style === "short") return `${String(y).slice(2)}.${match[2]}`;
  return `${y}년 ${m}월`;
}

/** 0.82 -> "82%" */
export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

/** 0.47 -> "0.47" */
export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return value.toFixed(2);
}

export const PHASE_LABELS: Record<Phase, string> = {
  발생: "발생",
  원인조사: "원인 조사",
  대응발표: "대응 발표",
  조치진행: "조치 진행",
  결과확인: "결과 확인",
  기타: "기타 관련 기사",
};

export const STRENGTH_LABELS: Record<Strength, string> = {
  확약: "확약",
  계획: "계획",
  의사표명: "의사표명",
  해당없음: "해당없음",
};

export const SIGNAL_LABELS: Record<SignalKind, string> = {
  완료: "완료",
  진행: "진행",
  새로운문제: "새로운 문제",
  무관: "무관",
  불명확: "불명확",
};

export const REPORT_TYPE_LABELS: Record<ReportType, string> = {
  wrong_link: "잘못된 연결",
  wrong_status: "상태 이의 제기",
  wrong_promise: "약속 추출 오류",
  other: "기타",
};

export const LINK_METHOD_LABELS: Record<string, string> = {
  seed: "시드",
  rule: "규칙",
  embedding: "임베딩",
  manual: "수동 확정",
};

/** Plain-Korean labels for rule thresholds shown on the evidence page. */
export const THRESHOLD_LABELS: Record<string, { label: string; unit?: string }> = {
  signal_min_conf: { label: "유효 신호로 인정하는 최소 신뢰도" },
  completion_min_conf: { label: "완료 판정에 필요한 최소 신뢰도" },
  stale_followup_days: { label: "후속보도 부족으로 보는 기준 일수", unit: "일" },
  progress_min_conf: { label: "진행 판정에 필요한 최소 신뢰도" },
  new_problem_min_conf: { label: "새로운 문제 판정에 필요한 최소 신뢰도" },
  drop_ratio_min: { label: "보도량 급감으로 보는 최소 감소 비율" },
};

export function formatThreshold(key: string, value: number): { label: string; value: string } {
  const meta = THRESHOLD_LABELS[key];
  if (!meta) return { label: key, value: String(value) };
  if (meta.unit) return { label: meta.label, value: `${value}${meta.unit}` };
  return { label: meta.label, value: value <= 1 ? `${value} (${formatPercent(value)})` : String(value) };
}
