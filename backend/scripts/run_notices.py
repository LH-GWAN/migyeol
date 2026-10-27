"""공공기관 게시판 확인 실행 (v2 §3·§7.3) — 관리자 API(POST /api/notices/run)와 같은 함수를 부른다.

  python scripts/run_notices.py --all                 # 활성 사건 전체 (트리거된 사건만 실제 요청)
  python scripts/run_notices.py --event-id 3
  python scripts/run_notices.py --check-sites         # 등록부의 각 게시판 목록 1~2쪽을 실제로 파싱해 본다 (docs/NOTICES.md 기록용)
  python scripts/run_notices.py --check-sites --site busan-press --query 안전
  python scripts/run_notices.py --rescore [--dry-run]     # 네트워크 없이 저장된 org_notices 를 현재 매칭 규칙으로 다시 점수 매긴다
  python scripts/run_notices.py --rescore 171 --dry-run   # 한 사건만, DB 는 바꾸지 않고 결과만 본다

트리거되지 않은 사건에는 어떤 요청도 보내지 않는다. 모든 요청은 허용 도메인·robots.txt·요청 간격을 거친다.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.session import init_db, session_factory  # noqa: E402
from app.notices.fetcher import HttpFetcher, NoticeFetchError  # noqa: E402
from app.notices.guard import BlockedURL  # noqa: E402
from app.notices.parser import parse_body, parse_list  # noqa: E402
from app.notices.service import rescore_all, run_checks  # noqa: E402
from app.notices.sites import load_sites  # noqa: E402


def check_sites(only: str | None, query: str | None, pages: int, detail: bool) -> list[dict]:
    settings = get_settings()
    sites = load_sites()
    rows = []
    with HttpFetcher(
        allowed_csv=settings.notice_allowed_domains,
        extra_domains=[s.domain for s in sites],
        user_agent=settings.notice_user_agent,
        interval_sec=settings.notice_request_interval_sec,
    ) as fetcher:
        for s in sites:
            if only and s.key != only:
                continue
            row = {"key": s.key, "name": s.name, "orgs": list(s.orgs), "url": s.first_page_url, "robots": None, "pages": [], "search": None, "detail": None, "error": None}
            try:
                row["robots"] = fetcher.robots(s.first_page_url).can_fetch(s.first_page_url)
                for p in range(1, pages + 1):
                    url = s.page_url(p)
                    items = parse_list(fetcher.get(url), s, url)
                    dated = [i.published_at for i in items if i.published_at]
                    row["pages"].append(
                        {
                            "page": p,
                            "items": len(items),
                            "dated": len(dated),
                            "newest": max(dated).isoformat() if dated else None,
                            "oldest": min(dated).isoformat() if dated else None,
                            "first": items[0].title[:60] if items else None,
                            "first_url": items[0].url if items else None,
                        }
                    )
                if query and s.search_url:
                    url = s.page_url(1, query)
                    items = parse_list(fetcher.get(url), s, url)
                    row["search"] = {"query": query, "items": len(items), "title_hits": sum(1 for i in items if query in i.title), "first": items[0].title[:60] if items else None}
                if detail and s.body and row["pages"] and row["pages"][0]["first_url"]:
                    body = parse_body(fetcher.get(row["pages"][0]["first_url"]), s)
                    row["detail"] = body[:80] if body else ""
            except (BlockedURL, NoticeFetchError) as exc:
                row["error"] = str(exc)[:200]
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        print(f"# requests={fetcher.requests} checked_at={date.today().isoformat()}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--event-id", type=int)
    g.add_argument("--check-sites", action="store_true")
    g.add_argument("--rescore", nargs="?", const="all", metavar="EVENT_ID", help="저장된 org_notices 재점수 (임계값 미만 삭제, 요청 없음). 사건 번호를 주면 그 사건만")
    ap.add_argument("--dry-run", action="store_true", help="--rescore: 결과만 출력하고 DB 는 바꾸지 않는다")
    ap.add_argument("--site", default=None, help="--check-sites: 이 key 만")
    ap.add_argument("--query", default=None, help="--check-sites: 게시판 검색도 시험")
    ap.add_argument("--pages", type=int, default=2, help="--check-sites: 목록 몇 쪽 (기본 2)")
    ap.add_argument("--detail", action="store_true", help="--check-sites: 첫 글 상세 본문 선택자도 시험")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (기본: TODAY_OVERRIDE 또는 오늘)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.check_sites:
        rows = check_sites(args.site, args.query, max(1, min(args.pages, get_settings().notice_max_pages)), args.detail)
        return 0 if all(r["pages"] and r["pages"][0]["items"] > 0 for r in rows) else 1

    settings = get_settings()
    init_db()
    session = session_factory()()
    try:
        if args.rescore is not None:
            out = rescore_all(session, settings, event_id=None if args.rescore == "all" else int(args.rescore), dry_run=args.dry_run)
            print_rescore(out)
            return 0
        today = date.fromisoformat(args.today) if args.today else None
        out = run_checks(session, settings, event_id=args.event_id, today=today)
    finally:
        session.close()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


def print_rescore(out: dict) -> None:
    """사건별 전후 건수 표 + 삭제·유지 항목 (docs/NOTICES.md 기록용)."""
    print(f"# rescore dry_run={out['dry_run']} events={out['events']} before={out['before']} after={out['after']} deleted={out['deleted']}")
    for r in out["results"]:
        print(f"\n## 사건 {r['event_id']} {r['title']} — {r['before']} → {r['after']}")
        for e in r["kept"]:
            print(f"  유지 {e['published_at']} {e['after']:.2f} {e['title']}  [{', '.join(e['matched'])}]")
        for e in r["dropped"]:
            print(f"  삭제 {e['published_at']} {e['before']:.2f}→{e['after']:.2f} {e['title']}  [{', '.join(e['matched'])}]")


if __name__ == "__main__":
    raise SystemExit(main())
