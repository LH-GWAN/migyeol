import { isEventStatus, type EventStatus } from "@/lib/types";

/**
 * Five fixed status colors (spec §11):
 * 초록 완료 / 파랑 진행 / 주황 새로운 문제 / 회색 후속보도 부족 / 점선 판단 불가
 * Any other string falls back to a neutral badge (defensive).
 */
const STYLES: Record<EventStatus, string> = {
  "조치 완료 근거 확인": "border-green-300 bg-green-50 text-green-800",
  "조치 진행 정황 확인": "border-blue-300 bg-blue-50 text-blue-800",
  "새로운 문제 발생": "border-orange-300 bg-orange-50 text-orange-800",
  "후속보도 부족": "border-gray-300 bg-gray-100 text-gray-700",
  "판단 불가": "border-dashed border-gray-500 bg-white text-gray-700",
};

const DOT: Record<EventStatus, string> = {
  "조치 완료 근거 확인": "bg-green-500",
  "조치 진행 정황 확인": "bg-blue-500",
  "새로운 문제 발생": "bg-orange-500",
  "후속보도 부족": "bg-gray-400",
  "판단 불가": "border border-dashed border-gray-500 bg-transparent",
};

const NEUTRAL = "border-gray-200 bg-gray-50 text-gray-500";

interface Props {
  status: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export default function StatusBadge({ status, size = "md", className = "" }: Props) {
  const known = isEventStatus(status);
  const sizeClass =
    size === "lg" ? "px-3 py-1 text-sm" : size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-0.5 text-xs";
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-semibold ${sizeClass} ${
        known ? STYLES[status] : NEUTRAL
      } ${className}`}
      title={known ? undefined : "정의되지 않은 상태 값"}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${known ? DOT[status] : "bg-gray-300"}`} aria-hidden />
      {status || "상태 없음"}
    </span>
  );
}
