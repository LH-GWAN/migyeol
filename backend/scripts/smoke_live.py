"""실 API 첫 호출 점검 (명세 §5.4): 7개 엔드포인트를 최소 인자로 한 번씩 부르고 실제 반환 키를 출력한다.

  BIGKINDS_ACCESS_KEY 는 .env 에서 읽는다 (출력하지 않는다).
  python scripts/smoke_live.py [--date 2026-10-10] [--query '"지하차도" AND "침수"']
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bigkinds.live import LiveBigKindsClient  # noqa: E402
from app.bigkinds.protocol import DEFAULT_NEWS_FIELDS, DEFAULT_QUOTATION_FIELDS, BigKindsError  # noqa: E402
from app.bigkinds.schemas import (  # noqa: E402
    ChangeTrackerArgument,
    NewsSearchArgument,
    PublishedAt,
    QuotationSearchArgument,
    TimeLineArgument,
    WordCloudArgument,
)
from app.config import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(date.today() - timedelta(days=2)).isoformat())
    ap.add_argument("--query", default='"지하차도" AND "침수"')
    args = ap.parse_args()
    settings = get_settings()
    if not settings.bigkinds_access_key:
        print("BIGKINDS_ACCESS_KEY 가 없다 (.env)")
        return 1
    key = settings.bigkinds_access_key
    c = LiveBigKindsClient(key, settings.bigkinds_base_url, rps=1.0)

    def redact(text: str) -> str:
        return text.replace(key, "<access_key>")  # 서버 오류 메시지에 키가 그대로 들어온다 (Invalid Access Key!:<key>)

    d = date.fromisoformat(args.date)
    window = PublishedAt.of(d - timedelta(days=40), d + timedelta(days=1))
    report: dict[str, object] = {}

    def step(name, fn):
        try:
            out = fn()
            report[name] = out
            print(f"[OK] {name}: {json.dumps(out, ensure_ascii=False)[:400]}")
        except BigKindsError as exc:
            report[name] = {"error": redact(str(exc))[:300]}
            print(f"[ERR] {name}: {redact(str(exc))[:300]}")

    def _news():
        r = c.search_news(NewsSearchArgument(query=args.query, published_at=window, sort={"date": "desc"}, return_size=3, fields=list(DEFAULT_NEWS_FIELDS)))
        docs = [doc.model_dump(exclude_none=True) for doc in r.documents]
        return {"total_hits": r.total_hits, "n": len(docs), "document_keys": sorted(docs[0].keys()) if docs else [], "sample": {k: (str(v)[:80]) for k, v in (docs[0].items() if docs else [])}}

    step("search_news", _news)
    first_id = None
    sample = report.get("search_news", {}).get("sample") if isinstance(report.get("search_news"), dict) else None
    if sample and sample.get("news_id"):
        first_id = sample["news_id"]

    def _by_ids():
        r = c.get_news_by_ids([first_id], fields=list(DEFAULT_NEWS_FIELDS) + ["content"])
        docs = [doc.model_dump(exclude_none=True) for doc in r.documents]
        return {"n": len(docs), "document_keys": sorted(docs[0].keys()) if docs else [], "content_len": len(docs[0].get("content") or "") if docs else None}

    if first_id:
        step("get_news_by_ids", _by_ids)

    def _issue():
        r = c.issue_ranking(d.isoformat())
        t = r.topics[0].model_dump() if r.topics else {}
        return {"date": r.date, "topics": len(r.topics), "topic_keys": sorted(t.keys()), "first": {"topic": t.get("topic"), "issue_category": t.get("issue_category"), "cluster": len(t.get("news_cluster") or [])}}

    step("issue_ranking", _issue)

    def _wc():
        r = c.word_cloud(WordCloudArgument(query='"지하차도"', published_at=window))
        return {"nodes": len(r.nodes), "top": [(n.name, n.weight) for n in sorted(r.nodes, key=lambda n: -n.weight)[:8]]}

    step("word_cloud", _wc)

    def _tl():
        r = c.time_line(TimeLineArgument(query=args.query, published_at=window, interval="month"))
        return {"total_hits": r.total_hits, "series": [(p.label, p.hits) for p in r.time_line]}

    step("time_line", _tl)

    def _quote():
        r = c.search_quotation(QuotationSearchArgument(query=args.query, published_at=window, return_size=3, fields=list(DEFAULT_QUOTATION_FIELDS)))
        docs = [doc.model_dump(exclude_none=True) for doc in r.documents]
        return {"total_hits": r.total_hits, "n": len(docs), "document_keys": sorted(docs[0].keys()) if docs else [], "sample": {k: (str(v)[:80]) for k, v in (docs[0].items() if docs else [])}}

    step("search_quotation", _quote)

    def _quote_text_only():
        # 인용문 검색이 기사 본문도 검색하는지 확인: 범위어(지역 등)만으로 인용문 밖 매칭이 되는지
        r = c.search_quotation(QuotationSearchArgument(query='"지하차도"', published_at=window, return_size=5, fields=list(DEFAULT_QUOTATION_FIELDS)))
        in_quote = sum(1 for doc in r.documents if "지하차도" in (doc.quotation or "") or "지하차도" in (doc.source or ""))
        return {"total_hits": r.total_hits, "returned": len(r.documents), "term_in_quotation_or_source": in_quote}

    step("search_quotation_scope", _quote_text_only)

    def _ct():
        r = c.change_tracker(ChangeTrackerArgument(**{"from": f"{d.isoformat()} 10:00:00", "interval": "MIN_15", "offset": "1"}))
        it = r.items[0].model_dump() if r.items else {}
        return {"total_count": r.total_count, "size": r.size, "items": len(r.items), "item_keys": sorted(it.keys()), "statuses": sorted({i.news_status for i in r.items})}

    step("change_tracker", _ct)

    print("\n--- call_log (실제 반환 키) ---")
    for row in c.call_log:
        print(json.dumps(row, ensure_ascii=False))
    out = Path(__file__).resolve().parent.parent / "data" / "smoke_live.json"  # data/ 는 git 제외
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(redact(json.dumps({"report": report, "call_log": c.call_log}, ensure_ascii=False, indent=1)), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
