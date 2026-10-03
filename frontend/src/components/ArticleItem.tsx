import { formatDate, formatScore, LINK_METHOD_LABELS } from "@/lib/format";
import type { ArticleOut } from "@/lib/types";

interface Props {
  article: ArticleOut;
  /** compact: single line meta + hilight, no link details */
  compact?: boolean;
  showLinkInfo?: boolean;
}

/** External link to the provider page (new tab); never renders full text — only `hilight`. */
export function ArticleLink({ article, className = "" }: { article: ArticleOut; className?: string }) {
  if (!article.provider_link_page) {
    return <span className={`font-medium text-gray-900 ${className}`}>{article.title}</span>;
  }
  return (
    <a
      href={article.provider_link_page}
      target="_blank"
      rel="noopener noreferrer"
      className={`font-medium text-gray-900 underline-offset-2 hover:text-blue-700 hover:underline ${className}`}
    >
      {article.title}
      <span aria-hidden className="ml-1 text-xs text-gray-400">
        ↗
      </span>
    </a>
  );
}

export function ArticleMeta({ article, className = "" }: { article: ArticleOut; className?: string }) {
  return (
    <span className={`text-xs text-gray-500 ${className}`}>
      {article.provider} · <time dateTime={article.published_at ?? undefined}>{formatDate(article.published_at)}</time>
      {article.byline ? ` · ${article.byline}` : ""}
      {article.change_status !== "ok" && (
        <span className="ml-1 rounded bg-amber-100 px-1 py-px text-[10px] font-semibold text-amber-800">
          {article.change_status === "cancelled" ? "기사 삭제됨" : "기사 수정됨"}
        </span>
      )}
    </span>
  );
}

export default function ArticleItem({ article, compact = false, showLinkInfo = true }: Props) {
  return (
    <li className={`${compact ? "py-2" : "py-3"} first:pt-0 last:pb-0`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <ArticleLink article={article} className={compact ? "text-sm" : "text-[15px]"} />
        <ArticleMeta article={article} />
      </div>
      {article.hilight && (
        <p className={`mt-1 ${compact ? "text-xs" : "text-sm"} leading-relaxed text-gray-600`}>{article.hilight}</p>
      )}
      {showLinkInfo && !compact && article.link_method && (
        <p className="mt-1 text-[11px] text-gray-400">
          연결: {LINK_METHOD_LABELS[article.link_method] ?? article.link_method} · 점수 {formatScore(article.link_score)}
          {article.link_reason ? ` · ${article.link_reason}` : ""}
        </p>
      )}
    </li>
  );
}
