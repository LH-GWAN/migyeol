"""파이프라인 단계별 실행.

  python scripts/run_pipeline.py --stage all --reset
  python scripts/run_pipeline.py --stage judge --event-id 3
  python scripts/run_pipeline.py --stage discover
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bigkinds import build_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Event  # noqa: E402
from app.db.session import init_db, session_factory  # noqa: E402
from app.llm.client import build_llm  # noqa: E402
from app.pipeline.embeddings import build_embedding  # noqa: E402
from app.pipeline.runner import STAGES, Pipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=STAGES, default="all")
    ap.add_argument("--event-id", type=int, default=None)
    ap.add_argument("--reset", action="store_true", help="테이블을 삭제하고 다시 만든다")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (기본: TODAY_OVERRIDE 또는 오늘)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    init_db(drop=args.reset)
    session = session_factory()()
    today = date.fromisoformat(args.today) if args.today else settings.today()
    pipe = Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings), today=today)
    run = pipe.run(args.stage, args.event_id)
    print(json.dumps({"run_id": run.id, "stage": run.stage, "ok": run.ok, "mode": run.mode, "summary": run.stats.get("summary")}, ensure_ascii=False))
    if args.stage in ("judge", "all"):
        print(f"{'id':>3} {'status':<14} {'reason':<24} {'prio':>6} {'followups':>9}  title")
        for e in session.query(Event).order_by(Event.priority_score.desc()).all():
            print(f"{e.id:>3} {e.status:<14} {e.status_reason or '':<24} {e.priority_score:>6} {e.followup_count:>9}  {e.title}")
    llm = pipe.llm
    if getattr(llm, "misses", None):
        print("stub misses:", llm.misses)
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
