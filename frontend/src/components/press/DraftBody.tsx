import type { ReactNode } from "react";
import type { PressSection, PressSource } from "@/lib/press";

/**
 * 보도자료 초안 본문 렌더러 (서버 컴포넌트). 백엔드가 주는 문단 텍스트를 그대로 보여 주되
 * - 빈 줄로 문단, `- ` 는 목록, `1. ` 은 번호 목록
 * - `[n]` 은 아래 「근거 기사」 n번으로 가는 위첨자 링크, http(s) 주소는 새 창 링크
 * 로만 바꾼다(마크다운 라이브러리 없이). 「근거 기사」 절은 sources 배열로 그린다.
 */

const TOKEN_RE = /(\[\d+\]|https?:\/\/[^\s)]+)/g;

export function Inline({ text }: { text: string }) {
  const parts = text.split(TOKEN_RE);
  return (
    <>
      {parts.map((p, i): ReactNode => {
        if (!p) return null;
        const ref = /^\[(\d+)\]$/.exec(p);
        if (ref) {
          return (
            <sup key={i} className="mx-0.5">
              <a href={`#src-${ref[1]}`} className="font-semibold text-blue-700 hover:underline" aria-label={`근거 기사 ${ref[1]}`}>
                [{ref[1]}]
              </a>
            </sup>
          );
        }
        if (/^https?:\/\//.test(p)) {
          return (
            <a key={i} href={p} target="_blank" rel="noopener noreferrer" className="break-all text-blue-700 hover:underline">
              {p}
            </a>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

function Block({ block }: { block: string }) {
  const lines = block.split("\n").filter((l) => l.trim() !== "");
  if (lines.length > 0 && lines.every((l) => /^- /.test(l))) {
    return (
      <ul className="ml-5 list-disc space-y-1.5">
        {lines.map((l, i) => (
          <li key={i}>
            <Inline text={l.replace(/^- /, "")} />
          </li>
        ))}
      </ul>
    );
  }
  // 「대표 사례」 같은 머리줄 + 번호 목록
  const numbered = lines.filter((l) => /^\d+\. /.test(l));
  if (numbered.length > 0 && numbered.length >= lines.length - 1) {
    const head = lines.filter((l) => !/^\d+\. /.test(l));
    return (
      <div>
        {head.map((h, i) => (
          <p key={i} className="mb-1.5 text-sm font-semibold text-gray-700">
            {h}
          </p>
        ))}
        <ol className="ml-5 list-decimal space-y-2">
          {numbered.map((l, i) => (
            <li key={i}>
              <Inline text={l.replace(/^\d+\. /, "")} />
            </li>
          ))}
        </ol>
      </div>
    );
  }
  return (
    <p className="whitespace-pre-line">
      <Inline text={lines.join("\n")} />
    </p>
  );
}

export function SourcesList({ sources }: { sources: PressSource[] }) {
  if (sources.length === 0) return <p className="text-sm text-gray-500">인용한 근거 기사가 없습니다.</p>;
  return (
    <ol className="space-y-1.5 text-sm">
      {sources.map((s) => (
        <li key={s.ref} id={`src-${s.ref}`} className="scroll-mt-24 target:rounded target:bg-yellow-50">
          <span className="mr-1 font-mono text-xs text-gray-500">[{s.ref}]</span>
          <span className="text-gray-600">{s.provider}</span>
          {", "}
          {s.link ? (
            <a href={s.link} target="_blank" rel="noopener noreferrer" className="font-medium text-blue-700 hover:underline">
              「{s.title}」
            </a>
          ) : (
            <span className="font-medium">「{s.title}」</span>
          )}
          <span className="text-gray-500">, {s.published_at ?? "날짜 미상"}</span>
        </li>
      ))}
    </ol>
  );
}

export default function DraftBody({ sections, sources }: { sections: PressSection[]; sources: PressSource[] }) {
  return (
    <div className="flex flex-col gap-6">
      {sections.map((s) => (
        <section key={s.heading} className="flex flex-col gap-2">
          <h3 className="border-l-4 border-gray-900 pl-2 text-base font-bold text-gray-900">{s.heading}</h3>
          {s.heading === "근거 기사" ? (
            <SourcesList sources={sources} />
          ) : (
            <div className="flex flex-col gap-3 text-[15px] leading-7 text-gray-800">
              {s.body.split(/\n{2,}/).map((b, i) => (
                <Block key={i} block={b} />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
