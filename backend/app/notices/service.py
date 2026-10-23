"""공공기관 게시판 확인 — 트리거 판정, 사건별 확인 실행, API 응답 조립 (v2 §3·§7.3).

트리거: 사건 상태가 `후속보도 부족`·`판단 불가`(판정된 사건만)이고
        max(마지막 후속 기사일, 가장 늦은 약속 발언일, 발생일) 로부터 `NOTICE_TRIGGER_DAYS` 이상 지났을 때.
        트리거되지 않은 사건은 어떤 사이트에도 요청하지 않는다(확인 기록만 남긴다).
대상:   사건 `responsible_org` 의 정규화 기관(canonical_org)에 등록된 게시판(sites.yaml)만.
범위:   목록 페이지 제목·게시일·URL(사이트당 `NOTICE_MAX_PAGES` 쪽, 발생일 이전 글이 나오면 멈춘다)
        + 제목이 매칭된 항목만 상세 본문 첫 300자.
의미:   보조 근거. 5개 상태를 바꾸지 않는다. 찾으면 `Event.org_notice_found=true`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..config import STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED, Settings
from ..db.models import Article, Event, StatusJudgment, _now
from ..rules.orgs import canonical_org
from .fetcher import Fetcher, HttpFetcher, NoticeFetchError
from .guard import BlockedURL
from .matcher import event_terms, score_title
from .models import NoticeCheck, OrgNotice
from .parser import parse_body, parse_list
from .sites import Site, load_sites, sites_for_org

log = logging.getLogger("migyeol.notices")

TRIGGER_STATUSES = (STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED)
DATE_SLACK_DAYS = 1  # 발생 전날 글까지 본다 (발생일 기준 보도자료가 전날 밤에 올라오는 경우)

OUTCOME_LABELS = {
    "not_triggered": "트리거 조건 미충족",
    "no_site": "등록된 게시판 없음",
    "not_checked": "아직 확인 전",
    "not_found": "확인했으나 없음",
    "found": "기관 발표 확인",
    "error": "확인 중 오류",
}


@dataclass
class Trigger:
    triggered: bool
    reason: str
    basis_date: date | None
    days: int | None


def _is_judged(session: Session, event: Event) -> bool:
    return session.query(StatusJudgment.id).filter_by(event_id=event.id).first() is not None


def latest_promise_date(session: Session, event: Event) -> date | None:
    """가장 늦은 약속 발언일: 인용문 날짜, 없으면 근거 기사 발행일. 병합된 약속·해당없음은 제외."""
    dates: list[date] = []
    for p in event.promises:
        if p.strength == "해당없음" or p.merged_into_id is not None:
            continue
        d = p.quotation.published_at if p.quotation is not None else None
        if d is None and p.evidence_news_id:
            a = session.get(Article, p.evidence_news_id)
            d = a.published_at if a is not None else None
        if d is not None:
            dates.append(d)
    return max(dates) if dates else None


def trigger_for(session: Session, event: Event, settings: Settings, today: date) -> Trigger:
    if not _is_judged(session, event):
        return Trigger(False, "아직 상태 판정 전인 사건", None, None)
    if event.status not in TRIGGER_STATUSES:
        return Trigger(False, f"상태가 '{event.status}' — 게시판 확인은 '후속보도 부족'·'판단 불가' 사건만", None, None)
    candidates = [d for d in (event.last_followup_at, latest_promise_date(session, event), event.occurred_at) if d is not None]
    basis = max(candidates)
    days = (today - basis).days
    if days < settings.notice_trigger_days:
        return Trigger(False, f"마지막 보도·약속 이후 {days}일 경과 (기준 {settings.notice_trigger_days}일 미만)", basis, days)
    return Trigger(True, f"'{event.status}' 상태로 마지막 보도·약속 이후 {days}일 경과 (기준 {settings.notice_trigger_days}일 이상)", basis, days)


def _search_queries(event: Event) -> list[str]:
    qs = [event.facility or ""] + [str(k) for k in (event.keywords or [])]
    out: list[str] = []
    for q in qs:
        q = " ".join(q.split())
        if len(q) >= 2 and q not in out:
            out.append(q)
    return out[:2]


def _scan_site(site: Site, event: Event, settings: Settings, fetcher: Fetcher, cutoff: date) -> tuple[list[dict], dict]:
    """한 게시판의 목록을 최대 notice_max_pages 쪽 읽고 매칭 항목을 돌려준다."""
    terms = event_terms(event.facility, event.keywords, event.promise_terms)
    info = {"key": site.key, "org": site.orgs[0], "name": site.name, "url": site.first_page_url, "pages": 0, "items": 0, "error": None}
    found: list[dict] = []
    seen: set[str] = set()
    queries: list[str | None] = _search_queries(event) if site.search_url else [None]
    budget = settings.notice_max_pages
    try:
        for q in queries:
            page = 1
            while budget > 0:
                url = site.page_url(page, q)
                html = fetcher.get(url)
                budget -= 1
                info["pages"] += 1
                items = parse_list(html, site, url)
                info["items"] += len(items)
                if not items:
                    break
                for it in items:
                    if it.url in seen or (it.published_at is not None and it.published_at < cutoff):
                        continue
                    seen.add(it.url)
                    score, matched = score_title(it.title, terms)
                    if score >= settings.notice_match_threshold:
                        found.append({"item": it, "score": score, "matched": matched})
                dated = [it.published_at for it in items if it.published_at is not None]
                if dated and max(dated) < cutoff:
                    break  # 최신순 목록에서 발생일 이전 글만 남았다
                page += 1
            if budget <= 0:
                break
    except BlockedURL as exc:
        info["error"] = f"차단: {exc}"
    except NoticeFetchError as exc:
        info["error"] = str(exc)[:200]
    # 매칭 항목만 상세 본문 첫 300자
    for f in found:
        it = f["item"]
        if not it.snippet and site.body:
            try:
                it.snippet = parse_body(fetcher.get(it.url), site)
            except (BlockedURL, NoticeFetchError) as exc:
                log.info("상세 본문 생략 %s: %s", it.url[:100], exc)
    return found, info


def _set_found_flag(event: Event, found: bool) -> None:
    """Event.org_notice_found 는 트랙 A 가 db/models.py 에 추가한다 — 아직 없으면 건너뛴다."""
    if hasattr(type(event), "org_notice_found"):
        event.org_notice_found = found


def check_event(
    session: Session,
    event: Event,
    settings: Settings,
    today: date | None = None,
    fetcher: Fetcher | None = None,
    sites: list[Site] | None = None,
) -> dict:
    """사건 하나를 확인한다. 트리거되지 않으면 요청 0건으로 기록만 남긴다."""
    today = today or settings.today()
    sites = sites if sites is not None else load_sites()
    trig = trigger_for(session, event, settings, today)
    org = canonical_org(event.responsible_org)
    check = NoticeCheck(event_id=event.id, triggered=trig.triggered, reason=trig.reason, checked_at=_now())
    summary = {"event_id": event.id, "title": event.title, "org": org, "triggered": trig.triggered, "reason": trig.reason, "requests": 0, "items_found": 0, "sites": []}
    if not trig.triggered:
        check.outcome = "not_triggered"
    else:
        targets = sites_for_org(org, sites)
        if not targets:
            check.outcome = "no_site"
            check.reason = f"{trig.reason} · '{org or '기관 미상'}' 에 등록된 게시판 없음"
        else:
            own = fetcher is None
            if own:
                fetcher = HttpFetcher(
                    allowed_csv=settings.notice_allowed_domains,
                    extra_domains=[s.domain for s in sites],
                    user_agent=settings.notice_user_agent,
                    interval_sec=settings.notice_request_interval_sec,
                )
            assert fetcher is not None
            cutoff = event.occurred_at - timedelta(days=DATE_SLACK_DAYS)
            total = 0
            infos = []
            try:
                for site in targets:
                    found, info = _scan_site(site, event, settings, fetcher, cutoff)
                    infos.append(info)
                    for f in found:
                        it = f["item"]
                        row = session.query(OrgNotice).filter_by(event_id=event.id, url=it.url).one_or_none() or OrgNotice(event_id=event.id, url=it.url)
                        row.org = org
                        row.site_key = site.key
                        row.site_url = site.first_page_url
                        row.title = it.title
                        row.published_at = it.published_at
                        row.snippet = it.snippet[:300]
                        row.match_score = f["score"]
                        row.matched_terms = f["matched"]
                        row.fetched_at = _now()
                        session.add(row)
                        total += 1
            finally:
                if own:
                    fetcher.close()  # type: ignore[union-attr]
            check.sites_checked = infos
            check.items_found = total
            check.requests = fetcher.requests
            errors = [i for i in infos if i["error"]]
            check.outcome = "found" if total else ("error" if errors and len(errors) == len(infos) else "not_found")
            summary.update(requests=fetcher.requests, items_found=total, sites=infos)
    session.add(check)
    session.flush()
    has_any = session.query(OrgNotice.id).filter_by(event_id=event.id).first() is not None
    _set_found_flag(event, has_any)
    session.commit()
    summary["outcome"] = check.outcome
    return summary


def run_checks(session: Session, settings: Settings, event_id: int | None = None, today: date | None = None, fetcher: Fetcher | None = None) -> dict:
    """트리거된 사건에 대해 확인을 실행한다 (CLI·관리자 API 공용). event_id 가 없으면 활성 사건 전체."""
    q = session.query(Event).filter(Event.active.is_(True))
    if event_id is not None:
        q = q.filter(Event.id == event_id)
    events = q.order_by(Event.id).all()
    results = [check_event(session, ev, settings, today=today, fetcher=fetcher) for ev in events]
    return {
        "events": len(results),
        "triggered": sum(1 for r in results if r["triggered"]),
        "found": sum(1 for r in results if r["outcome"] == "found"),
        "items": sum(r["items_found"] for r in results),
        "requests": sum(r["requests"] for r in results),
        "results": results,
    }


def _iso(d) -> str | None:
    if d is None:
        return None
    s = d.replace(microsecond=0).isoformat() if hasattr(d, "hour") else d.isoformat()
    return s + "+09:00" if hasattr(d, "hour") and d.tzinfo is None else s


def notices_out(session: Session, event: Event, settings: Settings, today: date | None = None) -> dict:
    """GET /api/notices/events/{id}. triggered·reason 은 요청 시점 기준, items 는 저장된 매칭 항목."""
    today = today or settings.today()
    trig = trigger_for(session, event, settings, today)
    org = canonical_org(event.responsible_org)
    targets = sites_for_org(org, load_sites())
    last = session.query(NoticeCheck).filter_by(event_id=event.id).order_by(NoticeCheck.id.desc()).first()
    rows = session.query(OrgNotice).filter_by(event_id=event.id).order_by(OrgNotice.published_at.desc(), OrgNotice.id.desc()).all()
    if rows:
        state = "found"
    elif not trig.triggered:
        state = "not_triggered"
    elif not targets:
        state = "no_site"
    elif last is None or not last.triggered:
        state = "not_checked"
    else:
        state = last.outcome if last.outcome in ("not_found", "error") else "not_found"
    return {
        "event_id": event.id,
        "org": org or None,
        "triggered": trig.triggered,
        "reason": trig.reason,
        "basis_date": _iso(trig.basis_date),
        "trigger_days": settings.notice_trigger_days,
        "state": state,
        "state_label": OUTCOME_LABELS[state],
        "checked_at": _iso(last.checked_at) if last else None,
        "sites": [{"org": s.orgs[0], "name": s.name, "url": s.first_page_url} for s in targets],
        "items": [
            {
                "title": r.title,
                "published_at": _iso(r.published_at),
                "url": r.url,
                "snippet": r.snippet,
                "match_score": r.match_score,
                "matched_terms": list(r.matched_terms or []),
                "site_name": next((s.name for s in targets if s.key == r.site_key), r.site_key),
            }
            for r in rows
        ],
    }
