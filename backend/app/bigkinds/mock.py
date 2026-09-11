"""픽스처 코퍼스를 실제 API 와 비슷한 검색 의미론으로 서비스하는 Mock 클라이언트 (예선용).

코퍼스 파일 (fixtures/bigkinds/):
  articles.json        : NewsDocument 형식 문서 목록 (content 는 200자 이내)
  quotations.json      : QuotationDocument 형식 인용문 목록
  issues.json          : {"YYYY-MM-DD": [IssueTopic ...]}
  change_tracker.json  : ChangeTrackerItem 목록

기본 반환 필드는 지침서 예시(news_id, title, published_at, enveloped_at, dateline, provider, hilight)를 따르고,
그 밖의 필드는 fields 에 명시한 것만 돌려준다 — 파이프라인이 fields 를 명시하도록 강제하기 위함.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .codes import normalize_incident, normalize_provider
from .query import Query
from .schemas import (
    ChangeTrackerArgument,
    ChangeTrackerResult,
    IssueRankingResult,
    NewsSearchArgument,
    NewsSearchResult,
    QuotationSearchArgument,
    QuotationSearchResult,
    TimeLineArgument,
    TimeLineResult,
    WordCloudArgument,
    WordCloudResult,
    _SearchBase,
    parse_published,
)

_DEFAULT_DOC_FIELDS = ("news_id", "title", "published_at", "enveloped_at", "dateline", "provider")
_DEFAULT_QUOTE_FIELDS = ("news_id", "published_at", "date", "provider", "source", "quotation")


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


class MockBigKindsClient:
    mode = "mock"

    def __init__(self, fixtures_dir: Path):
        self.dir = Path(fixtures_dir)
        self.articles: list[dict[str, Any]] = _load(self.dir / "articles.json", [])
        self.quotations: list[dict[str, Any]] = _load(self.dir / "quotations.json", [])
        self.issues: dict[str, list[dict[str, Any]]] = _load(self.dir / "issues.json", {})
        self.changes: list[dict[str, Any]] = _load(self.dir / "change_tracker.json", [])
        self._by_id = {a["news_id"]: a for a in self.articles}
        self.call_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _text(doc: dict[str, Any], scope: str = "all") -> str:
        if scope == "title":
            return doc.get("title", "")
        if scope == "content":
            return doc.get("content", "")
        return " ".join(str(doc.get(k) or "") for k in ("title", "content", "tms_raw_stream")).replace("\n", " ")

    @staticmethod
    def _query_of(arg: _SearchBase) -> tuple[Query, str]:
        q = arg.query
        if isinstance(q, dict):
            if "title" in q:
                return Query(q["title"]), "title"
            if "content" in q:
                return Query(q["content"]), "content"
            return Query(""), "all"
        return Query(q or ""), "all"

    def _passes_filters(self, doc: dict[str, Any], arg: _SearchBase) -> bool:
        if arg.published_at is not None:
            d = parse_published(doc.get("published_at"))
            if d is None:
                return False
            start = date.fromisoformat(arg.published_at.from_)
            until = date.fromisoformat(arg.published_at.until)
            if not (start <= d < until):
                return False
        if arg.provider:
            wanted = {normalize_provider(p) for p in arg.provider}
            if normalize_provider(doc.get("provider", "")) not in wanted:
                return False
        if arg.category:
            cats = doc.get("category") or []
            if not any(c.startswith(w) for w in arg.category for c in cats):
                return False
        if arg.category_incident:
            wanted_inc = [normalize_incident(w) for w in arg.category_incident]
            incs = [normalize_incident(c) for c in (doc.get("category_incident") or [])]
            if not any(c.startswith(w) for w in wanted_inc for c in incs):
                return False
        return True

    def _search_docs(self, docs: list[dict[str, Any]], arg: _SearchBase, text_fn) -> tuple[list[dict[str, Any]], Query]:
        query, scope = self._query_of(arg)
        out = []
        for doc in docs:
            if not self._passes_filters(doc, arg):
                continue
            if query.matches(text_fn(doc, scope)):
                out.append(doc)
        return out, query

    @staticmethod
    def _sort(docs: list[dict[str, Any]], sort: Any, query: Query, text_fn) -> list[dict[str, Any]]:
        spec = sort if isinstance(sort, list) else [sort or {}]
        primary = spec[0] if spec else {}
        if "date" in primary:
            reverse = primary["date"] == "desc"
            return sorted(docs, key=lambda d: d.get("published_at") or "", reverse=reverse)
        if "title" in primary:
            return sorted(docs, key=lambda d: d.get("title") or "")
        return sorted(docs, key=lambda d: (-query.score(text_fn(d, "all")), d.get("published_at") or ""))

    @staticmethod
    def _hilight(text: str, query: Query, limit: int) -> str:
        if not text:
            return ""
        terms = [t for t in query.terms() if t]
        snippet = text
        if terms:
            low = text.lower()
            first = min((low.find(t) for t in terms if low.find(t) >= 0), default=0)
            start = max(0, first - 40)
            snippet = text[start:]
            for t in terms:
                snippet = re.sub(re.escape(t), lambda m: f"<b>{m.group(0)}</b>", snippet, count=1, flags=re.IGNORECASE)
        return snippet[:limit]

    def _project(self, doc: dict[str, Any], fields: list[str], hilight: int, query: Query, defaults: tuple[str, ...], hl_source: str) -> dict[str, Any]:
        out = {k: doc.get(k) for k in defaults if k in doc}
        for f in fields:
            if f in doc:
                out[f] = doc[f]
        if hilight and hilight > 0:
            out["hilight"] = self._hilight(doc.get(hl_source) or "", query, hilight)
        return out

    def _log(self, endpoint: str, arg: Any, n: int, total: int) -> None:
        q = arg.get("query") if isinstance(arg, dict) else getattr(arg, "query", None)
        self.call_log.append({"endpoint": endpoint, "query": q, "total_hits": total, "n": n})

    # ---------------------------------------------------------- endpoints
    def search_news(self, arg: NewsSearchArgument) -> NewsSearchResult:
        docs, query = self._search_docs(self.articles, arg, self._text)
        docs = self._sort(docs, arg.sort, query, self._text)
        total = len(docs)
        page = docs[arg.return_from : arg.return_from + arg.return_size]
        projected = [self._project(d, arg.fields, arg.hilight, query, _DEFAULT_DOC_FIELDS, "content") for d in page]
        self._log("/search/news", arg.argument(), len(projected), total)
        return NewsSearchResult(total_hits=total, documents=projected)

    def get_news_by_ids(self, news_ids: list[str], fields: list[str] | None = None) -> NewsSearchResult:
        fields = fields or []
        docs = [self._by_id[i] for i in news_ids if i in self._by_id]
        projected = [self._project(d, fields, 0, Query(""), _DEFAULT_DOC_FIELDS, "content") for d in docs]
        self._log("/search/news(news_ids)", {"query": f"{len(news_ids)} ids"}, len(projected), len(projected))
        return NewsSearchResult(total_hits=len(projected), documents=projected)

    def issue_ranking(self, date: str, provider: list[str] | None = None) -> IssueRankingResult:
        topics = self.issues.get(date, [])
        if provider:
            wanted = {normalize_provider(p) for p in provider}
            topics = [
                t
                for t in topics
                if any(normalize_provider(self._by_id.get(n, {}).get("provider", "")) in wanted for n in t.get("news_cluster", []))
            ]
        self._log("/issue_ranking", {"query": date}, len(topics), len(topics))
        return IssueRankingResult(date=date, topics=topics)

    def word_cloud(self, arg: WordCloudArgument) -> WordCloudResult:
        docs, query = self._search_docs(self.articles, arg, self._text)
        qterms = set(query.terms())
        counter: Counter[str] = Counter()
        for d in docs:
            for kw in {k.strip() for k in (d.get("tms_raw_stream") or "").split("\n") if k.strip()}:
                if kw.lower() in qterms or any(t in kw.lower() for t in qterms):
                    continue
                counter[kw] += 1
        top = counter.most_common(30)
        peak = top[0][1] if top else 1
        nodes = [{"id": i + 2, "name": name, "level": 3, "weight": round(2.0 * c / peak, 2)} for i, (name, c) in enumerate(top)]
        self._log("/word_cloud", arg.argument(), len(nodes), len(docs))
        return WordCloudResult(nodes=nodes)

    def time_line(self, arg: TimeLineArgument) -> TimeLineResult:
        docs, _ = self._search_docs(self.articles, arg, self._text)
        buckets: dict[str, int] = defaultdict(int)
        for d in docs:
            pd = parse_published(d.get("published_at"))
            if pd is None:
                continue
            label = {"day": pd.strftime("%Y%m%d"), "month": pd.strftime("%Y%m"), "year": pd.strftime("%Y")}[arg.interval]
            buckets[label] += 1
        series = [{"label": k, "hits": buckets[k]} for k in sorted(buckets)]
        self._log("/time_line", arg.argument(), len(series), len(docs))
        return TimeLineResult(total_hits=len(docs), time_line=series)

    def search_quotation(self, arg: QuotationSearchArgument) -> QuotationSearchResult:
        def qtext(doc: dict[str, Any], scope: str = "all") -> str:
            art = self._by_id.get(doc["news_id"], {})
            return " ".join([doc.get("source", ""), doc.get("quotation", ""), art.get("title", ""), art.get("content", "")])

        docs, query = self._search_docs(self.quotations, arg, qtext)
        docs = self._sort(docs, arg.sort, query, qtext)
        total = len(docs)
        page = docs[arg.return_from : arg.return_from + arg.return_size]
        projected = [self._project(d, arg.fields, arg.hilight, query, _DEFAULT_QUOTE_FIELDS, "quotation") for d in page]
        self._log("/search/quotation", arg.argument(), len(projected), total)
        return QuotationSearchResult(total_hits=total, documents=projected)

    def change_tracker(self, arg: ChangeTrackerArgument) -> ChangeTrackerResult:
        upto = datetime.strptime(arg.from_, "%Y-%m-%d %H:%M:%S")
        unit, n = arg.interval.split("_")
        span = timedelta(minutes=int(n)) if unit == "MIN" else timedelta(days=int(n))
        since = upto - span
        rows = []
        for item in self.changes:
            try:
                upd = datetime.strptime(item.get("update_dt", ""), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if since <= upd <= upto:
                rows.append(item)
        size = 3000
        page = max(1, int(arg.offset or "1"))
        chunk = rows[(page - 1) * size : page * size]
        self._log("/changeTracker/news", {"query": arg.from_}, len(chunk), len(rows))
        return ChangeTrackerResult(total_count=len(rows), size=size, message="OK", items=chunk)
