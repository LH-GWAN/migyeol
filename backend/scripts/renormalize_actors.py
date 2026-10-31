"""저장된 약속의 발언 주체(actor_org)를 현재 규칙으로 다시 정규화한다 — 10-30 rules-2·fe-1·press-1 (실명·'부시' 주체 정리).

  BIGKINDS_MODE=record BIGKINDS_REPLAY_ONLY=true LLM_MODE=rules .venv/bin/python scripts/renormalize_actors.py --dry-run
  ...                                                           .venv/bin/python scripts/renormalize_actors.py

네트워크 없음(이름+직함 검색은 캐시만). 약속 id·행동·기한은 그대로라 추적 약속 집합 해시가 바뀌지 않는다(재분류 없음).
병합 키(actor_org)가 바뀌므로 extract 를 다시 돌리면 병합이 달라질 수 있다 — 여기서는 병합을 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import Event, Promise, Quotation  # noqa: E402
from app.db.session import init_db, session_factory  # noqa: E402
from app.llm.rules_adapter import actor_from_source  # noqa: E402
from app.pipeline.extract import resolve_actor_org  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--event-id", type=int, default=None)
    args = ap.parse_args()
    init_db()
    session = session_factory()()
    q = session.query(Promise, Quotation, Event).join(Quotation, Quotation.id == Promise.quotation_id).join(Event, Event.id == Promise.event_id)
    if args.event_id:
        q = q.filter(Event.id == args.event_id)
    changes = Counter()
    n = 0
    for p, quote, ev in q.all():
        lexicon = [ev.region, ev.responsible_org, *(ev.org_aliases or [])]
        new = resolve_actor_org(ev, quote, actor_from_source(quote.source, ev.responsible_org), lexicon)
        if new != (p.actor_org or ""):
            changes[(p.actor_org or "", new or "")] += 1
            n += 1
            if not args.dry_run:
                p.actor_org = new
    if not args.dry_run:
        session.commit()
    print(f"{'would change' if args.dry_run else 'changed'}: {n}")
    for (old, new), k in changes.most_common(40):
        print(f"  {k:4}  {old!r} -> {new!r}")
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
