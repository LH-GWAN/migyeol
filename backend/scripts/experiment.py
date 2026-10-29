"""v2 §4 실험: 원천 기사 100건 → 자동 시드 → 사건 군집 → 후속보도 탐지 → 지표 리포트.

  BIGKINDS_MODE=record LLM_MODE=rules EMBEDDING_BACKEND=st .venv/bin/python scripts/experiment.py --stage all --dry-run
  BIGKINDS_MODE=record LLM_MODE=rules EMBEDDING_BACKEND=st .venv/bin/python scripts/experiment.py --stage all
  .venv/bin/python scripts/experiment.py --stage report                   # 사람 라벨(CSV)을 채운 뒤 지표만 다시
  .venv/bin/python scripts/experiment.py --stage all --seeds seeds/events.yaml   # 사람이 확정한 시드로 collect 이후 다시
  BIGKINDS_MODE=record ... scripts/experiment.py --stage collect --seeds seeds/events.yaml --today 2026-10-22 --events x20250807-134   # 고친 사건만 다시 수집

단계: discover(원천 인용문 수집) · seed(유니크화 → seeds/experiment.draft.yaml) · collect · link · followup(extract+judge) · report.
기준일은 --today(기본: 오늘, KST). .env 의 TODAY_OVERRIDE(가상 픽스처용)는 쓰지 않는다 — 녹화 재생은 같은 --today 로.
호출 계획이 §4.7 예산(5,000회)의 2배를 넘으면 실행하지 않고 멈춘다(--force 로 무시).
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
from app.config import BACKEND_DIR, get_settings, kst_today  # noqa: E402
from app.db.session import init_db, session_factory  # noqa: E402
from app.experiment.runner import BUDGET, STAGES, Experiment, ExperimentConfig, prefixes_from  # noqa: E402
from app.llm.client import build_llm  # noqa: E402
from app.pipeline.embeddings import HashEmbedding, build_embedding  # noqa: E402
from app.redact import mask  # noqa: E402


def _embedder(settings):
    if settings.embedding_backend != "st":
        return build_embedding(settings), ""
    try:
        return build_embedding(settings), ""
    except Exception as exc:  # noqa: BLE001 — 설치·다운로드 실패는 hash 로 대체하고 리포트에 남긴다
        return HashEmbedding(), f"EMBEDDING_BACKEND=st 로드 실패({type(exc).__name__}) — hash 로 실행"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=STAGES, default="all")
    ap.add_argument("--dry-run", action="store_true", help="호출 계획만 출력")
    ap.add_argument("--seeds", default=None, help="사람이 확정한 시드 파일 (collect 이후만 실행)")
    ap.add_argument("--today", default=None, help="기준일 YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--until", dest="date_until", default=None)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--max-events", type=int, default=150)
    ap.add_argument("--no-issue", action="store_true", help="이슈 랭킹 보조 경로 생략")
    ap.add_argument("--issue-stride", type=int, default=1, help="이슈 랭킹 날짜 간격(일)")
    ap.add_argument("--workers", type=int, default=4, help="원천 수집 동시 요청 수 (시작 간격은 BIGKINDS_RPS)")
    ap.add_argument("--force", action="store_true", help="호출 계획이 예산의 2배를 넘어도 실행")
    ap.add_argument("--events", default="", help="collect 를 이 사건 key 들만 (쉼표 구분, 예: x20250807-134,x20240429-052)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "sentence_transformers", "migyeol.bigkinds", "migyeol.collect", "migyeol.link", "migyeol.extract", "migyeol.judge"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = get_settings()
    today = date.fromisoformat(args.today) if args.today else kst_today()
    seeds = Path(args.seeds) if args.seeds else None
    if seeds is not None and not seeds.is_absolute():
        seeds = BACKEND_DIR / seeds
    from app.experiment import sources

    cfg = ExperimentConfig(
        start=date.fromisoformat(args.date_from) if args.date_from else settings.experiment_from,
        until=date.fromisoformat(args.date_until) if args.date_until else settings.experiment_until,
        today=today,
        prefixes=prefixes_from(settings),
        max_pages=args.max_pages or sources.MAX_PAGES,
        issue=not args.no_issue,
        issue_stride=args.issue_stride,
        max_events=args.max_events,
        seeds_path=seeds,
        workers=max(1, args.workers),
        query_hit_limit=settings.experiment_query_hit_limit,
        max_articles_per_event=settings.experiment_max_articles_per_event,
        only_keys=tuple(k.strip() for k in args.events.split(",") if k.strip()),
    )

    plan = sources.call_plan(cfg.start, cfg.until, cfg.prefixes, cfg.max_pages, cfg.issue, cfg.max_events, cfg.issue_stride)
    if seeds is not None:
        plan = {k: v for k, v in plan.items() if k.startswith("collect")}
        plan["total"] = sum(plan.values())
    print("호출 계획(상한 기준):")
    for k, v in plan.items():
        print(f"  {k:<44} {v:>6}")
    print(f"  §4.7 예산 {BUDGET}회 · 2배 한도 {BUDGET * 2}회 · BIGKINDS_RPS={settings.bigkinds_rps} → 약 {plan['total'] / max(settings.bigkinds_rps, 0.1) / 60:.0f}분 (녹화 재생분 제외)")
    if args.dry_run:
        return 0
    if plan["total"] > BUDGET * 2 and not args.force:
        print("호출 계획이 예산의 2배를 넘어 멈춘다 (--force 로 무시)")
        return 2
    if settings.bigkinds_mode == "mock":
        print("경고: BIGKINDS_MODE=mock — 가상 픽스처로 실행한다 (본선 산출물은 record)")

    emb, note = _embedder(settings)
    cfg.embedding_note = note
    init_db()
    session = session_factory()()
    try:
        exp = Experiment(session, settings, build_client(settings), build_llm(settings), emb, cfg)
        out = exp.run(args.stage)
    except Exception as exc:  # noqa: BLE001
        print("실험 실패:", mask(f"{type(exc).__name__}: {exc}")[:500])
        raise SystemExit(1) from None
    finally:
        session.close()
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
