import type { PressSection, PressSource } from "@/lib/press";
import { Inline, SourcesList } from "./DraftBody";

/**
 * 「기사체」 초안 본문 렌더러 (서버 컴포넌트). 절 제목 없이 문단만 — 리드는 조금 크게, 본문·배경·마무리는 본문 크기,
 * 「근거 기사」 는 브리프와 같은 목록. `[n]` 과 주소 링크 처리는 DraftBody 의 Inline 을 같이 쓴다.
 */
export default function ArticleBody({ sections, sources }: { sections: PressSection[]; sources: PressSource[] }) {
  return (
    <div className="flex flex-col gap-6">
      {sections.map((s, idx) => {
        if (s.heading === "근거 기사") {
          return (
            <section key={s.heading} className="flex flex-col gap-2 border-t border-gray-100 pt-5">
              <h3 className="text-base font-bold text-gray-900">{s.heading}</h3>
              <SourcesList sources={sources} />
            </section>
          );
        }
        const paragraphs = s.body.split(/\n{2,}/).filter((b) => b.trim() !== "");
        const isLead = idx === 0;
        const isClosing = s.heading === "마무리";
        return (
          <section key={s.heading} aria-label={s.heading} className="flex flex-col gap-4">
            {paragraphs.map((b, i) => (
              <p
                key={i}
                className={
                  isLead
                    ? "text-[17px] font-medium leading-8 text-gray-900"
                    : isClosing
                      ? "text-sm leading-7 text-gray-600"
                      : "text-[15px] leading-7 text-gray-800"
                }
              >
                <Inline text={b.replace(/\n/g, " ")} />
              </p>
            ))}
          </section>
        );
      })}
    </div>
  );
}
