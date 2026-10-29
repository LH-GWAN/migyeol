import Link from "next/link";

/** 상단 「기관」 메뉴의 두 항목 (2026-10-29 피드백). /orgs 와 /orgs/notices 상단에 같은 탭을 둔다 — 드롭다운 대신 모바일에서도 깨지지 않는 탭. */
export const ORG_TABS = [
  { href: "/orgs", label: "기관별 후속보도 현황" },
  { href: "/orgs/notices", label: "기관 발표로 확인된 사례" },
] as const;

export type OrgTab = (typeof ORG_TABS)[number]["href"];

export default function OrgTabs({ current }: { current: OrgTab }) {
  return (
    <nav aria-label="기관 메뉴" className="flex gap-1 overflow-x-auto border-b border-gray-200 whitespace-nowrap">
      {ORG_TABS.map((t) => {
        const active = t.href === current;
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={active ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold ${
              active ? "border-gray-900 text-gray-900" : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
