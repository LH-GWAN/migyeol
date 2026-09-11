import { DISCLAIMER } from "@/lib/constants";

interface Props {
  /** The `disclaimer` string from the API. Falls back to the fixed sentence if missing. */
  text?: string | null;
  className?: string;
}

/** Fixed disclaimer shown next to every status display (spec §2-3). */
export default function Disclaimer({ text, className = "" }: Props) {
  return (
    <p className={`flex items-start gap-1.5 text-xs leading-relaxed text-gray-500 ${className}`}>
      <span aria-hidden className="mt-px shrink-0 text-gray-400">
        ※
      </span>
      <span>{text || DISCLAIMER}</span>
    </p>
  );
}
