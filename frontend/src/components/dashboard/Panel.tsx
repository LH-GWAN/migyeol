import Disclaimer from "@/components/Disclaimer";

interface Props {
  id?: string;
  title: string;
  subtitle?: string;
  /** 상태·후속보도 숫자를 보여 주는 패널이면 면책 문구를 붙인다 */
  disclaimer?: string | null;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

/** 대시보드 카드 패널 */
export default function Panel({ id, title, subtitle, disclaimer, action, className = "", children }: Props) {
  return (
    <section id={id} className={`flex min-w-0 scroll-mt-6 flex-col gap-4 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm sm:p-5 ${className}`}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-base font-bold text-gray-900">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-gray-500">{subtitle}</p>}
        </div>
        {action}
      </header>
      {children}
      {disclaimer !== undefined && <Disclaimer text={disclaimer} className="mt-auto border-t border-gray-100 pt-3" />}
    </section>
  );
}
