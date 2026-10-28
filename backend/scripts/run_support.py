"""민간 지원 소식 재생성 (docs/SUPPORT.md §4) — 외부 요청 없이 저장된 articles 행만 읽는다.

  python scripts/run_support.py --all              # 활성 사건 전체의 support_mentions 를 지우고 다시 만든다 (멱등)
  python scripts/run_support.py --event 164        # 한 사건만 (--event-id 도 같다)
  python scripts/run_support.py --all --dump       # 사건별 행을 함께 출력 (정밀도 표본 읽기용)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import init_db, session_factory  # noqa: E402
from app.support.models import SupportMention  # noqa: E402
from app.support.service import rebuild_all  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--event", "--event-id", dest="event_id", type=int)
    ap.add_argument("--dump", action="store_true", help="저장된 행을 사건별로 출력")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    init_db()
    session = session_factory()()
    try:
        out = rebuild_all(session, event_id=args.event_id)
        print(json.dumps({k: v for k, v in out.items() if k != "results"}, ensure_ascii=False))
        for r in out["results"]:
            if r["mentions"]:
                print(f"사건 {r['event_id']} {r['title']}: 기사 {r['articles']} · 언급 {r['mentions']} · 주체 {r['actors']} · 당사자 {r['parties']}")
        if args.dump:
            q = session.query(SupportMention).order_by(SupportMention.event_id, SupportMention.published_at, SupportMention.id)
            if args.event_id is not None:
                q = q.filter(SupportMention.event_id == args.event_id)
            for m in q.all():
                print(json.dumps({"id": m.id, "event_id": m.event_id, "news_id": m.news_id, "published_at": str(m.published_at), "actor": m.actor, "kind": m.actor_kind, "role": m.actor_role, "terms": m.support_terms, "evidence": m.evidence_span}, ensure_ascii=False))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
