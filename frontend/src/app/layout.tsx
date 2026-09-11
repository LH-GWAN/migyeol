import type { Metadata } from "next";
import SiteHeader from "@/components/SiteHeader";
import { DISCLAIMER, SERVICE_NAME, TAGLINE } from "@/lib/constants";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: `${SERVICE_NAME} — 뉴스 후속 확인`,
    template: `%s | ${SERVICE_NAME}`,
  },
  description: TAGLINE,
};

// Every page reads live backend data; never prerender against the API at build time.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <SiteHeader />
        <main className="mx-auto w-full max-w-[1100px] flex-1 px-4 py-8">{children}</main>
        <footer className="border-t border-gray-200 bg-white">
          <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-1 px-4 py-5 text-xs text-gray-500">
            <p>
              {SERVICE_NAME} · 공공기관·지자체의 공개된 공식 발표만 추적합니다. 기사 원문은 언론사 페이지에서 확인하세요.
            </p>
            <p>{DISCLAIMER}</p>
          </div>
        </footer>
      </body>
    </html>
  );
}
