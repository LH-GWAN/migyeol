"""GET /api/events/{id}/support — 가상 픽스처 DB(mock 파이프라인) + 가상 기사 행. 외부 요청 없음.

저장(rebuild_event, 멱등)·당사자 표시·공공 주체 제외·개인 익명화·발생일 이전 기사 제외·응답 형태를 고정한다.
사건 상태는 바꾸지 않는다.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import Article, Event, EventArticle
from app.db.session import init_db, reset_engine, session_factory
from app.support import service
from app.support.models import SupportMention
from app.support.service import SUPPORT_DISCLAIMER


@pytest.fixture
def session(e2e_db):
    from app.config import get_settings

    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = e2e_db
    reset_engine()
    init_db(e2e_db)
    s = session_factory()()
    s.query(SupportMention).delete()
    s.commit()
    yield s
    s.query(SupportMention).delete()
    for a in s.query(Article).filter(Article.news_id.like("09900001.%")).all():
        s.delete(a)
    s.commit()
    s.close()


@pytest.fixture
def client(session):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _ev(session, title_part: str) -> Event:
    return next(e for e in session.query(Event).all() if title_part in e.title)


def _add(session, ev: Event, n: int, title: str, hilight: str, days: int, provider: str = "가온일보", link: str | None = "https://example.test/a") -> Article:
    """가상 기사 한 건을 사건에 활성 연결한다. days 는 발생일로부터의 날짜."""
    a = Article(
        news_id=f"09900001.{20250000000000 + n:020d}"[:64],
        title=title,
        hilight=hilight,
        content_200=None,
        tms_keywords=[],
        published_at=ev.occurred_at + timedelta(days=days),
        provider=provider,
        provider_link_page=link,
    )
    session.add(a)
    session.flush()
    session.add(EventArticle(event_id=ev.id, news_id=a.news_id, phase="기타", link_method="manual", link_score=1.0))
    session.commit()
    return a


def test_rebuild_event_stores_private_actors_only_and_is_idempotent(session):
    ev = _ev(session, "달빛지하차도")
    status_before = (ev.status, ev.status_reason, ev.followup_count)
    _add(session, ev, 1, "가온은행, 달빛지하차도 침수 피해 복구 성금 1억원 기탁", "가온은행은 침수 피해 상인들을 위해 성금 1억원을 가온사회복지공동모금회에 기탁했다고 밝혔다", 5)
    _add(session, ev, 2, "가온시, 침수 피해 상인에 성금 전달", "가온시는 피해 상인들에게 성금을 전달했다", 6)  # 공공 주체 — 기관 발표 영역
    _add(session, ev, 3, "김모씨 익명 기부", "김모씨는 익명으로 침수 피해 복구 성금 500만원을 기탁했다", 7, link=None)
    _add(session, ev, 4, "누리그룹, 작년 수해 성금", "누리그룹은 피해 복구 성금을 기탁했다", -30)  # 발생일 이전
    out = service.rebuild_event(session, ev)
    assert out["mentions"] == 2 and out["articles"] == 2
    rows = session.query(SupportMention).filter_by(event_id=ev.id).order_by(SupportMention.published_at).all()
    assert [(r.actor, r.actor_kind, r.actor_role) for r in rows] == [("가온은행", "기업", "제3자"), ("개인", "개인", "제3자")]
    assert "성금" in rows[0].support_terms and rows[0].evidence_span.startswith("가온은행은") and len(rows[0].evidence_span) <= 200
    assert "김모" not in rows[1].actor and "김모" in rows[1].evidence_span  # 이름은 주체로 저장하지 않는다 (근거 문구는 메타데이터 범위)
    # 다시 돌려도 같은 행 수 (멱등), 상태는 그대로
    service.rebuild_event(session, ev)
    assert session.query(SupportMention).filter_by(event_id=ev.id).count() == 2
    session.refresh(ev)
    assert (ev.status, ev.status_reason, ev.followup_count) == status_before


def test_party_flag_uses_event_description(session):
    ev = _ev(session, "달빛지하차도")
    saved = ev.summary
    ev.summary = (saved or "") + " 배수펌프 시공사는 한빛건설이다."
    session.commit()
    try:
        _add(session, ev, 5, "한빛건설, 침수 피해 주민에 위로금 전달", "한빛건설은 침수 피해 주민들에게 위로금을 전달했다", 8)
        service.rebuild_event(session, ev)
        row = session.query(SupportMention).filter_by(event_id=ev.id, actor="한빛건설").one()
        assert row.actor_role == "당사자"
    finally:
        ev.summary = saved
        session.commit()


def test_api_shape_and_404(client, session):
    ev = _ev(session, "달빛지하차도")
    body = client.get(f"/api/events/{ev.id}/support").json()
    assert body["event_id"] == ev.id and body["items"] == [] and body["count"] == 0
    assert body["disclaimer"] == SUPPORT_DISCLAIMER and "상태(5종)를 바꾸지 않습니다" in body["note"]

    _add(session, ev, 6, "가온은행, 침수 피해 복구 성금 기탁", "가온은행은 침수 피해 복구 성금 1억원을 기탁했다", 5)
    _add(session, ev, 7, "다솜 봉사단, 피해 상인 위해 350만원 기탁", "다솜 봉사단이 피해 상인들을 위해 성금을 기탁했다", 9, link=None)
    service.rebuild_event(session, ev)
    body = client.get(f"/api/events/{ev.id}/support").json()
    assert body["count"] == 2 and body["actors"] == 2
    items = body["items"]
    assert [i["actor"] for i in items] == ["가온은행", "다솜 봉사단"]  # 날짜 오름차순
    assert set(items[0]) == {"news_id", "title", "published_at", "provider", "provider_link_page", "actor", "actor_kind", "actor_role", "support_terms", "evidence_span"}
    assert items[0]["published_at"] == (ev.occurred_at + timedelta(days=5)).isoformat()
    assert items[0]["provider_link_page"] == "https://example.test/a" and items[1]["provider_link_page"] is None
    assert items[1]["actor_kind"] == "단체" and items[0]["actor_role"] == "제3자"

    assert client.get("/api/events/999999/support").status_code == 404
    assert client.get("/api/events/0/support").status_code == 422


def test_rebuild_all_counts(session):
    ev = _ev(session, "달빛지하차도")
    _add(session, ev, 8, "가온은행, 침수 피해 복구 성금 기탁", "가온은행은 침수 피해 복구 성금 1억원을 기탁했다", 5)
    out = service.rebuild_all(session)
    assert out["events"] == 8 and out["events_with_mentions"] == 1 and out["mentions"] == 1
    one = service.rebuild_all(session, event_id=ev.id)
    assert one["events"] == 1 and one["mentions"] == 1
    assert date.today() >= ev.occurred_at
