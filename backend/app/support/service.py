"""민간 지원 소식 — 저장(멱등 재생성)과 API 출력. 상태 판정(rules/status.py·pipeline/judge.py)은 건드리지 않는다."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..db.models import Article, Event, EventArticle
from ..experiment.text import scrub
from .extractor import Mention, assign_roles, event_text, extract_article
from .models import SupportMention

log = logging.getLogger("migyeol.support")

SUPPORT_DISCLAIMER = "연결 기사에서 규칙으로 뽑은 지원 언급이며 사건 상태와 무관합니다"
SUPPORT_NOTE = "기업·단체·개인의 피해 복구·구호 지원 언급을 빅카인즈 메타데이터(제목·발췌·핵심어)에서 규칙으로 뽑았습니다. 보조 근거이며 사건 상태(5종)를 바꾸지 않습니다."


def _active_articles(session: Session, event: Event) -> list[tuple[EventArticle, Article]]:
    rows = (
        session.query(EventArticle, Article)
        .join(Article, Article.news_id == EventArticle.news_id)
        .filter(EventArticle.event_id == event.id, EventArticle.rejected.is_(False), EventArticle.needs_review.is_(False))
        .all()
    )
    return [(l, a) for l, a in rows if a.published_at is not None and a.published_at >= event.occurred_at]


def extract_event(session: Session, event: Event) -> list[tuple[Article, Mention]]:
    blob = event_text(event.title, event.summary, event.facility, event.responsible_org, event.keywords, event.org_aliases)
    out: list[tuple[Article, Mention]] = []
    for _link, art in sorted(_active_articles(session, event), key=lambda la: (la[1].published_at, la[1].news_id)):
        ms = extract_article(art.title, art.hilight, art.content_200, art.tms_keywords, party_blob=blob)
        for m in assign_roles(ms, blob):
            out.append((art, m))
    return out


def rebuild_event(session: Session, event: Event) -> dict:
    """사건의 support_mentions 를 지우고 다시 만든다 (멱등). 반환: 건수 요약."""
    found = extract_event(session, event)
    session.query(SupportMention).filter(SupportMention.event_id == event.id).delete(synchronize_session="fetch")
    seen: set[tuple[str, str]] = set()
    rows = 0
    for art, m in found:
        key = (art.news_id, m.actor)
        if key in seen:
            continue
        seen.add(key)
        session.add(
            SupportMention(
                event_id=event.id,
                news_id=art.news_id,
                actor=m.actor,
                actor_kind=m.actor_kind,
                actor_role=m.actor_role,
                support_terms=m.support_terms,
                evidence_span=m.evidence_span,
                published_at=art.published_at,
            )
        )
        rows += 1
    session.commit()
    actors = {m.actor for _a, m in found}
    return {"event_id": event.id, "title": event.title, "articles": len({a.news_id for a, _m in found}), "mentions": rows, "actors": len(actors), "parties": sum(1 for _a, m in found if m.actor_role == "당사자")}


def rebuild_all(session: Session, event_id: int | None = None) -> dict:
    q = session.query(Event).filter(Event.active.is_(True))
    if event_id is not None:
        q = q.filter(Event.id == event_id)
    results = [rebuild_event(session, ev) for ev in q.order_by(Event.id).all()]
    return {
        "events": len(results),
        "events_with_mentions": sum(1 for r in results if r["mentions"] > 0),
        "mentions": sum(r["mentions"] for r in results),
        "articles": sum(r["articles"] for r in results),
        "results": results,
    }


def _iso(d) -> str | None:
    return d.isoformat() if d is not None else None


def support_out(session: Session, event: Event) -> dict:
    rows = (
        session.query(SupportMention, Article)
        .join(Article, Article.news_id == SupportMention.news_id)
        .filter(SupportMention.event_id == event.id)
        .order_by(SupportMention.published_at.asc(), SupportMention.news_id.asc(), SupportMention.id.asc())
        .all()
    )
    items = [
        {
            "news_id": m.news_id,
            "title": scrub(a.title),
            "published_at": _iso(m.published_at),
            "provider": a.provider,
            "provider_link_page": a.provider_link_page,
            "actor": scrub(m.actor),
            "actor_kind": m.actor_kind,
            "actor_role": m.actor_role,
            "support_terms": list(m.support_terms or []),
            "evidence_span": scrub(m.evidence_span),  # 실기사 문장의 단정 표현은 화면에 내지 않는다 (10-30 A1)
        }
        for m, a in rows
    ]
    return {
        "event_id": event.id,
        "count": len(items),
        "actors": len({i["actor"] for i in items}),
        "items": items,
        "note": SUPPORT_NOTE,
        "disclaimer": SUPPORT_DISCLAIMER,
    }
