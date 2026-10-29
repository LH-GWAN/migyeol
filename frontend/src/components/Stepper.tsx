import { formatDate, PHASE_LABELS } from "@/lib/format";
import type { Phase, TimelinePhase } from "@/lib/types";

export const STEP_PHASES: readonly Phase[] = ["발생", "원인조사", "대응발표", "조치진행", "결과확인"];

interface Props {
  timeline: TimelinePhase[];
  /** 사건 발생일 — '발생' 단계에 연결된 기사가 없어도 발생 사실(날짜)은 알고 있다. */
  occurredAt?: string | null;
}

/** '발생' 이 비었을 때의 문구: 발생일은 시드로 확인되고, 첫 보도 기사만 연결되지 않은 상태. */
export function occurrenceEmptyText(occurredAt: string | null | undefined): string {
  return occurredAt ? `${formatDate(occurredAt)} 발생 · 첫 보도 미연결` : "첫 보도 미연결";
}

/** Horizontal 5-step stepper. Filled steps show article counts; empty steps say so explicitly. */
export default function Stepper({ timeline, occurredAt }: Props) {
  const countByPhase = new Map<Phase, number>();
  for (const t of timeline) countByPhase.set(t.phase, t.articles.length);

  return (
    <ol className="grid grid-cols-5 gap-2" aria-label="사건 진행 단계">
      {STEP_PHASES.map((phase, i) => {
        const count = countByPhase.get(phase) ?? 0;
        const filled = count > 0;
        return (
          <li key={phase} className="relative flex min-w-0 flex-col items-center text-center">
            {i < STEP_PHASES.length - 1 && (
              <span
                aria-hidden
                className={`absolute left-1/2 top-4 h-0.5 w-full ${filled && (countByPhase.get(STEP_PHASES[i + 1]) ?? 0) > 0 ? "bg-gray-800" : "bg-gray-200"}`}
              />
            )}
            <a
              href={`#phase-${phase}`}
              className={`relative z-10 flex h-8 w-8 items-center justify-center rounded-full border-2 text-sm font-bold ${
                filled
                  ? "border-gray-900 bg-gray-900 text-white"
                  : "border-dashed border-gray-400 bg-white text-gray-400"
              }`}
              aria-label={`${PHASE_LABELS[phase]} 단계로 이동`}
            >
              {i + 1}
            </a>
            <span className={`mt-2 text-sm font-semibold ${filled ? "text-gray-900" : "text-gray-400"}`}>
              {PHASE_LABELS[phase]}
            </span>
            <span className={`mt-0.5 text-xs leading-snug ${filled ? "text-gray-600" : "text-gray-400"}`}>
              {filled ? (
                `기사 ${count}건`
              ) : phase === "발생" && occurredAt ? (
                <>
                  {formatDate(occurredAt)} 발생
                  <br />
                  <span className="whitespace-nowrap">첫 보도 미연결</span>
                </>
              ) : phase === "발생" ? (
                occurrenceEmptyText(occurredAt)
              ) : (
                "이 단계의 보도를 찾지 못함"
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
