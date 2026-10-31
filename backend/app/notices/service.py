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
from ..experiment.text import scrub
from ..rules.orgs import canonical_org
from .fetcher import Fetcher, HttpFetcher, NoticeFetchError
from .guard import BlockedURL
from .matcher import event_terms, own_regions, score_title
from .models import NoticeCheck, OrgNotice
from .parser import parse_body, parse_list
from .sites import Site, load_sites, sites_for_org

log = logging.getLogger("migyeol.notices")

TRIGGER_STATUSES = (STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED)
DATE_SLACK_DAYS = 1  # 발생 전날 글까지 본다 (발생일 기준 보도자료가 전날 밤에 올라오는 경우)
SAME_ANNOUNCEMENT_DAYS = 1  # 원천 기사 발행일 ±1일의 게시판 글은 같은 발표로 본다 ('뉴스에는 없었지만' 아님)

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


def source_article_date(session: Session, event: Event) -> date | None:
    """원천 기사(source_news_id)의 발행일. 같은 날 같은 발표를 옮긴 게시판 글은 '뉴스에는 없었지만' 의 근거가 아니다 (10-30 exp-1·notice-3)."""
    if event.source_news_id:
        a = session.get(Article, event.source_news_id)
        if a is not None and a.published_at is not None:
            return a.published_at
    return None


def first_statement_date(session: Session, event: Event) -> date:
    """가장 이른 공공 대응 발언일(추적 약속 인용문 날짜), 없으면 발생일 — 기관 발표가 이 뒤에 나와야 '발표 이후' 다."""
    dates: list[date] = []
    for p in event.promises:
        if not p.is_trackable or p.merged_into_id is not None:
            continue
        d = p.quotation.published_at if p.quotation is not None else None
        if d is not None:
            dates.append(d)
    first = min(dates) if dates else None
    return max(d for d in (first, event.occurred_at) if d is not None)


def notice_specific(event: Event, org: str | None, notice) -> bool:
    """발표가 이 사건을 가리키는가 — 매칭 용어가 사건 시설명과 겹치고, 시설명이 일반어('하천 제방')면 지역명도 제목·용어에 있어야 한다
    (대시보드 사례와 같은 조건 — 195 거문도·283 다른 호우 같은 무관한 발표를 '뉴스엔 없었지만' 으로 쓰지 않는다)."""
    from .matcher import is_generic, own_regions

    def _norm(t: str) -> str:
        return "".join(str(t or "").split())

    fac = _norm(event.facility)
    terms = list(notice.matched_terms or [])
    if len(fac) >= 2:
        hit = False
        for raw in terms:
            t = str(raw)
            if t.startswith("+요약:"):
                t = t.split(":", 1)[1]
            elif t.startswith("+"):
                continue
            tn = _norm(t)
            if len(tn) >= 2 and (tn in fac or fac in tn):
                hit = True
                break
        if not hit:
            return False
    if not event.facility or is_generic(event.facility):
        own = own_regions(event.region, org)
        if own:
            blob = _norm(" ".join(str(t) for t in terms) + " " + (notice.title or ""))
            if not any(_norm(o) in blob for o in own):
                return False
    return True


def is_news_gap_notice(session: Session, event: Event, published_at: date | None) -> bool:
    """이 게시판 글을 '뉴스에는 없었지만 기관은 발표했다' 사례로 쓸 수 있는가 —
    발생·첫 대응 발언 뒤에 나왔고, 원천 기사와 같은 발표(발행일 ±1일)가 아니어야 한다."""
    if published_at is None:
        return False
    if published_at <= first_statement_date(session, event):
        return False
    src = source_article_date(session, event)
    return src is None or abs((published_at - src).days) > SAME_ANNOUNCEMENT_DAYS


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


def _event_terms(event: Event):
    """사건의 매칭 용어 — 시설명·핵심어·약속어 + 지역명(책임기관 자신의 이름이 아닐 때)."""
    return event_terms(event.facility, event.keywords, event.promise_terms, region=event.region, org=canonical_org(event.responsible_org))


def _own_regions(event: Event) -> set[str]:
    return own_regions(event.region, canonical_org(event.responsible_org))


def _own_notice(site: Site, event: Event, item_org: str) -> bool:
    """여러 기관이 함께 쓰는 게시판(정책브리핑)에서 발표 주체가 사건 책임기관(또는 별칭)이 아닌 글은 거른다 (10-30 notice-4).
    발표 주체를 읽지 못했거나 기관 하나짜리 게시판이면 통과."""
    if not item_org or len(site.orgs) <= 1:
        return True
    cand = canonical_org(item_org)
    mine = {canonical_org(x) for x in (event.responsible_org, *(event.org_aliases or [])) if x}
    return bool(cand) and cand in mine  # 부분 문자열 비교는 안 한다 — '경찰청' 글이 '해양경찰청' 사건을 통과하면 안 된다


def _scan_site(site: Site, event: Event, settings: Settings, fetcher: Fetcher, cutoff: date) -> tuple[list[dict], dict]:
    """한 게시판의 목록을 최대 notice_max_pages 쪽 읽고 매칭 항목을 돌려준다."""
    terms = _event_terms(event)
    info = {"key": site.key, "org": site.orgs[0], "name": site.name, "url": site.first_page_url, "pages": 0, "items": 0, "error": None, "other_org": 0}
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
                    if not _own_notice(site, event, it.org):
                        info["other_org"] += 1
                        continue  # 다른 부처의 발표
                    # 요약 가점은 목록 페이지가 주는 요약에만 적용된다 (상세 본문은 매칭된 항목만 뒤에 가져온다)
                    m = score_title(it.title, terms, snippet=it.snippet, published_at=it.published_at, occurred_at=event.occurred_at, own=_own_regions(event))
                    if m.score >= settings.notice_match_threshold:
                        found.append({"item": it, "score": m.score, "matched": m.labels})
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
        targets = sites_for_org(event.responsible_org, sites)
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
                        row.org = it.org or org  # 발표 주체를 읽었으면 그 이름 (정책브리핑 글을 책임기관 게시판 글로 표기하지 않는다)
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


class NoticesBusy(RuntimeError):
    """다른 프로세스(CLI·API·스케줄러)가 게시판 확인을 실행 중이다."""


def _run_lock_path():
    from ..config import BACKEND_DIR
    from ..db.session import db_path

    p = db_path()
    base = p.parent if p is not None and p.name != ":memory:" else BACKEND_DIR / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "notices.lock"


def run_checks(session: Session, settings: Settings, event_id: int | None = None, today: date | None = None, fetcher: Fetcher | None = None) -> dict:
    """트리거된 사건에 대해 확인을 실행한다 (CLI·관리자 API 공용). event_id 가 없으면 활성 사건 전체.
    파일 잠금으로 CLI·API·스케줄러가 겹쳐 돌지 않게 한다 (겹치면 NoticesBusy — 10-30 notice-2)."""
    import fcntl

    fh = open(_run_lock_path(), "a+")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise NoticesBusy("기관 게시판 확인이 다른 프로세스에서 실행 중이다") from exc
        return _run_checks(session, settings, event_id, today, fetcher)
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _run_checks(session: Session, settings: Settings, event_id: int | None, today: date | None, fetcher: Fetcher | None) -> dict:
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


def rescore_event(session: Session, event: Event, settings: Settings, dry_run: bool = False) -> dict:
    """저장된 org_notices 를 현재 매칭 규칙으로 다시 점수 매긴다 (네트워크 없음).

    임계값 미만은 삭제하고, 남은 항목의 점수·일치 용어를 갱신한다. `Event.org_notice_found` 와
    가장 최근 실제 확인 기록(`notice_checks`, 결과 found/not_found)의 `items_found`·`outcome` 을 남은 건수에 맞춘다.
    dry_run 이면 결과만 계산하고 DB 는 바꾸지 않는다.
    """
    terms = _event_terms(event)
    rows = session.query(OrgNotice).filter_by(event_id=event.id).order_by(OrgNotice.id).all()
    kept: list[dict] = []
    dropped: list[dict] = []
    for r in rows:
        m = score_title(r.title, terms, snippet=r.snippet, published_at=r.published_at, occurred_at=event.occurred_at, own=_own_regions(event))
        entry = {"id": r.id, "title": r.title, "published_at": r.published_at, "before": r.match_score, "after": m.score, "matched": m.labels}
        if m.score >= settings.notice_match_threshold:
            kept.append(entry)
            if not dry_run:
                r.match_score = m.score
                r.matched_terms = m.labels
        else:
            dropped.append(entry)
            if not dry_run:
                session.delete(r)
    if not dry_run:
        session.flush()
        _set_found_flag(event, bool(kept))
        last = (
            session.query(NoticeCheck)
            .filter(NoticeCheck.event_id == event.id, NoticeCheck.triggered.is_(True), NoticeCheck.outcome.in_(("found", "not_found")))
            .order_by(NoticeCheck.id.desc())
            .first()
        )
        if last is not None:
            last.items_found = len(kept)
            last.outcome = "found" if kept else "not_found"
        session.commit()
    return {"event_id": event.id, "title": event.title, "before": len(rows), "after": len(kept), "kept": kept, "dropped": dropped}


def rescore_all(session: Session, settings: Settings, event_id: int | None = None, dry_run: bool = False) -> dict:
    """org_notices 가 있는 사건 전체(또는 한 사건)를 다시 점수 매긴다."""
    q = session.query(Event).filter(Event.id.in_(session.query(OrgNotice.event_id).distinct()))
    if event_id is not None:
        q = q.filter(Event.id == event_id)
    results = [rescore_event(session, ev, settings, dry_run=dry_run) for ev in q.order_by(Event.id).all()]
    return {
        "dry_run": dry_run,
        "events": len(results),
        "before": sum(r["before"] for r in results),
        "after": sum(r["after"] for r in results),
        "deleted": sum(len(r["dropped"]) for r in results),
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
    targets = sites_for_org(event.responsible_org, load_sites())
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
    # '뉴스에는 없었지만 기관 발표' 라고 말할 수 있는 사건 = 상태가 후속보도 부족·판단 불가이고, 발표가 원천 발언(마지막 약속 발언일) 뒤에 나온 것
    # (10-30 fe-3·notice-3). 그렇지 않은 사건(완료 상태, 같은 날 같은 발표)에는 중립 문구를 쓴다
    gap_items = [r for r in rows if is_news_gap_notice(session, event, r.published_at) and notice_specific(event, org, r)]
    news_gap = bool(gap_items) and event.status in TRIGGER_STATUSES
    return {
        "event_id": event.id,
        "org": org or None,
        "triggered": trig.triggered,
        "news_gap": news_gap,
        "news_gap_items": len(gap_items),
        "reason": trig.reason,
        "basis_date": _iso(trig.basis_date),
        "trigger_days": settings.notice_trigger_days,
        "state": state,
        "state_label": OUTCOME_LABELS[state],
        "checked_at": _iso(last.checked_at) if last else None,
        "sites": [{"org": s.orgs[0], "name": s.name, "url": s.first_page_url} for s in targets],
        "items": [
            {
                "title": scrub(r.title),
                "published_at": _iso(r.published_at),
                "url": r.url,
                "snippet": scrub(r.snippet),  # 기관 글 제목·요약도 단정 표현은 가린다 (10-30 A1)
                "match_score": r.match_score,
                "matched_terms": list(r.matched_terms or []),
                "site_name": next((s.name for s in targets if s.key == r.site_key), r.site_key),
            }
            for r in rows
        ],
    }
