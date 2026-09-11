import { API_BASE, type ApiError } from "@/lib/api";

interface Props {
  title?: string;
  error?: ApiError | null;
  message?: string;
  hint?: string;
}

/** Korean error panel used whenever a backend call fails — pages never crash. */
export default function ErrorBox({ title = "자료를 불러오지 못했습니다", error, message, hint }: Props) {
  const main = message ?? error?.message ?? "요청 처리 중 오류가 발생했습니다.";
  const detail = error?.detail;
  const connection = error && error.status === undefined;
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-900">
      <p className="font-semibold">{title}</p>
      <p className="mt-1">{main}</p>
      {detail && <p className="mt-1 break-all font-mono text-xs text-red-700/80">{detail}</p>}
      {(hint || connection) && (
        <p className="mt-3 text-xs text-red-800/80">
          {hint ??
            `백엔드(${API_BASE})가 실행 중인지 확인하세요. 백엔드 없이 화면만 확인하려면 NEXT_PUBLIC_USE_MOCK=1 로 실행할 수 있습니다.`}
        </p>
      )}
    </div>
  );
}
