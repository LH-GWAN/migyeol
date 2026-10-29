"""'이름 + 직함' 발언 주체의 기관 검색 캐시를 채운다 (rules/actor_search.py, docs/EXPERIMENT.md §15).

  BIGKINDS_MODE=record BIGKINDS_REPLAY_ONLY=false .venv/bin/python scripts/resolve_actors.py --dry-run   # 예상 호출 수만
  BIGKINDS_MODE=record BIGKINDS_REPLAY_ONLY=false .venv/bin/python scripts/resolve_actors.py             # 실 호출 → 녹화 + fixtures/actor_orgs.json
  BIGKINDS_MODE=record BIGKINDS_REPLAY_ONLY=true  .venv/bin/python scripts/resolve_actors.py --summary   # 캐시로 해석·보류 목록만

대상: DB 의 공공 발언 인용문 중 이름+직함이고 기존 규칙(normalize_actor_org) 결과가 아는 기관이 아닌 것 (actor_search.wants). 호출은 (이름, 핵심 직함, 분기)당 1회.
1,000회를 넘으면 --limit 으로 빈도 상위부터.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bigkinds import build_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Event, Quotation  # noqa: E402
from app.db.session import init_db, session_factory  # noqa: E402
from app.rules import actor_search  # noqa: E402
from app.rules.actors import normalize_actor_org, person_title  # noqa: E402


def _items(session):
    out = []
    for q, e in session.query(Quotation, Event).join(Event, Event.id == Quotation.event_id).filter(Quotation.is_public_actor.is_(True)):
        lexicon = [e.region, e.responsible_org, *(e.org_aliases or [])]
        if not actor_search.wants(q.source, normalize_actor_org(q.source, e.responsible_org, e.org_aliases or []), lexicon):
            continue
        out.append((q.source, q.published_at, lexicon, e))
    return out


def summary(items) -> dict:
    """(이름, 직함) 단위: 인용문 수, 해석된 기관(사건 지역 반영)별 인용문 수, 보류 사유."""
    by_pair: dict[tuple[str, str], Counter] = defaultdict(Counter)
    reasons: dict[tuple[str, str], Counter] = defaultdict(Counter)
    docs: dict[tuple[str, str, str], int] = {}
    entries = actor_search.load_cache()["entries"]
    for source, published, lex, e in items:
        pt = person_title(source, [x for x in lex if x])
        if not pt or published is None:
            continue
        ent = entries.get(actor_search.cache_key(*pt, actor_search.bucket_of(published)))
        if not ent:
            by_pair[pt]["(캐시 없음)"] += 1
            continue
        d = actor_search.decide(ent.get("dist") or {}, e.region)
        by_pair[pt][d.org or "(보류)"] += 1
        if d.org:
            docs[(*pt, d.org)] = max(docs.get((*pt, d.org), 0), d.docs)
        else:
            reasons[pt][d.reason] += 1
    rows = []
    for pt, c in sorted(by_pair.items(), key=lambda kv: -sum(kv[1].values())):
        rows.append({"name": pt[0], "title": pt[1], "quotes": sum(c.values()), "orgs": dict(c.most_common()),
                     "docs": {o: docs.get((*pt, o)) for o in c if (*pt, o) in docs}, "held": dict(reasons[pt].most_common())})
    return {"pairs": len(rows), "quotes": sum(r["quotes"] for r in rows),
            "resolved_quotes": sum(n for r in rows for o, n in r["orgs"].items() if not o.startswith("(")), "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true", help="검색하지 않고 캐시로 해석·보류 목록만")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None, help="요약 JSON 저장 경로")
    args = ap.parse_args()
    settings = get_settings()
    init_db()
    session = session_factory()()
    items = _items(session)
    todo = actor_search.needed((s, p, lex) for s, p, lex, _ in items)
    pairs = {(n, t) for n, t, _ in todo}
    print(f"이름+직함 공공 인용문(기존 규칙이 기관을 못 찾음) {len(items)}건 · 캐시에 없는 (이름, 직함, 분기) {len(todo)}개 = 예상 호출 {len(todo)}회 (고유 이름·직함 {len(pairs)}쌍)")
    if len(todo) > 1000 and args.limit is None:
        print("1,000회 초과 — --limit 로 빈도 상위부터 돌린다")
        return 1
    if not args.dry_run and not args.summary and todo:
        client = build_client(settings)
        stats = actor_search.ensure_cache(client, ((s, p, lex) for s, p, lex, _ in items), limit=args.limit)
        print("검색:", json.dumps(stats, ensure_ascii=False), "· 녹화 재생", getattr(client, "hits", "-"), "· 새 호출", getattr(client, "misses", "-"))
    if args.dry_run:
        return 0
    out = summary(items)
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(f"해석 {out['resolved_quotes']}/{out['quotes']} 인용문 · {out['pairs']}쌍")
    for r in out["rows"]:
        print(f"  {r['name']} {r['title']} ({r['quotes']}) → {r['orgs']} 근거 {r['docs']} {r['held'] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
