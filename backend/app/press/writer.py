"""보도자료 초안 작성기 — v2 §6 화면 4·§7.2.

PressWriter 프로토콜 두 구현:
- TemplateWriter : 지금 쓰는 작성기. 문장 틀은 string.Template (의존성 없음). 숫자·사례는 전부 data.py 집계에서 온다.
- ArticleWriter  : 「기사체」(format=article). 같은 data.py 집계로 제목→부제→리드→본문→배경→마무리 문단을 쓴다. 글머리표·표 없음.
- LLMWriter      : LLM_MODE=live 일 때만. 위 두 작성기 초안의 문장만 다듬는다(prompts/press_v1.md). 숫자·근거 번호[n]가
                   하나라도 바뀌거나 금지 표현이 들어오면 그 절은 템플릿 문장을 그대로 쓴다.

문장 규칙: 모든 사례 문장은 근거 기사 번호 [n] 을 단다(sources 와 같은 번호). 표현은 "확인됐다 / 확인되지 않았다" 만 쓰고
기관을 평가·비난하지 않는다. 보도가 없다는 사실은 조치가 없었다는 근거가 아니다(DISCLAIMER 고정).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from string import Template
from typing import Protocol

from ..config import DISCLAIMER, KST, Settings
from . import data as D

log = logging.getLogger("migyeol.press")

HEADER_NOTE = "이 초안은 미결이 빅카인즈 API로 수집·분석한 결과를 바탕으로 자동 작성했습니다. 배포 전 사람이 검토합니다."
NOT_JUDGMENT = "이 자료는 기관의 조치 여부를 판정하지 않으며, 보도와 기관 공식 게시판에서 확인된 사실만 정리했습니다."
CONTACT = "팀 후속취재단 · 담당 [이름] · [이메일] · [전화번호] · 서비스 [주소]"
SIGNAL_TEXT = {"완료": "조치 완료", "진행": "조치 진행", "새로운문제": "새로운 문제"}
SOURCES_HEADING = "근거 기사"
FORMATS = ("brief", "article")

T = {
    "title_all": Template("재난·안전 사건 ${events}건 추적 결과, ${followup}건에서 후속보도 확인 — 뉴스 애프터케어 서비스 ‘미결’"),
    "lead": Template(
        "팀 후속취재단은 한국언론진흥재단 빅카인즈 API로 ${period} 재난·안전 사건 보도를 분석한 결과, "
        "공공기관의 대응 발언이 담긴 원천 기사 ${sources}건에서 사건 ${events}건을 확인했고, "
        "이 가운데 ${followup}건(${rate})에서 발표 이후 조치를 다룬 후속보도가 확인됐다고 밝혔다. "
        "나머지 ${rest}건은 ${as_of} 기준으로 후속보도가 확인되지 않았다."
    ),
    "lead_exp": Template("원천 기사는 빅카인즈 인용문 검색으로 모았고, 같은 사건을 다룬 기사끼리 묶어 사건 ${events}건으로 정리했다. (실험 리포트 ${generated} 기준)"),
    "orgs_intro": Template("후속보도가 확인된 기관은 ${count}곳이다. ${names}."),
    "orgs_none": Template("${as_of} 기준으로 후속보도가 확인된 기관은 없다."),
    "case_followup": Template("${org_josa} ${spoken} “${action}”${rago} 밝혔고${pref}, ${pub} ${provider} 보도 「${title}」에서 ‘${span}’ 내용이 확인됐다(${signal}) [${ref}]."),
    "gap_intro": Template("후속보도가 확인되지 않은 사건은 ${count}건이다. 이 사건들은 발표 이후의 조치를 다룬 보도가 ${as_of} 기준으로 확인되지 않았다는 뜻이며, 조치가 없었다는 뜻은 아니다."),
    "case_gap": Template("${org_josa} ${spoken} “${action}”${rago} 밝혔으나 [${ref}], ${since} 이후 이 약속과 관련된 후속보도는 확인되지 않았다."),
    "case_gap_nopromise": Template("${occurred} 발생 이후 공공기관의 구체적인 대응 발언과 후속보도가 확인되지 않았다."),
    "notice_none": Template("후속보도가 확인되지 않은 채 마지막 보도·약속 이후 ${days}일 이상 지난 사건이 없어, 기관 공식 게시판 확인 대상이 없었다."),
    "notice_intro": Template(
        "후속보도가 확인되지 않은 채 마지막 보도·약속 이후 ${days}일 이상 지난 사건 ${triggered}건은 책임기관의 공식 보도자료·공지사항 게시판 확인 대상이다. "
        "결과는 ${breakdown}이다."
    ),
    "notice_item": Template("${title}: ${org} 게시판 「${notice}」(${date}) — ${url}"),
    "method": Template(
        "분석에는 빅카인즈 OpenAPI의 뉴스 검색·인용문 검색·키워드 트렌드·연관어 분석·뉴스 변경이력을 사용했다. "
        "사건 발생 후 공공기관이 대응을 밝힌 인용문을 원천 기사로 삼아 같은 사건 기사를 규칙과 문장 유사도로 묶고, "
        "발표 이후 기사에서 약속 행동과 겹치는 완료·진행·새로운 문제 표현(신뢰도 ${min_conf} 이상)을 찾아 사건 상태를 정하고, 상태가 조치 완료 근거 확인·조치 진행 정황 확인·새로운 문제 발생인 사건을 ‘후속보도 확인’으로 셌다. "
        "분석 기간은 ${period}, 기준일은 ${as_of}이며, 사건 상태는 조치 완료 근거 확인·조치 진행 정황 확인·새로운 문제 발생·후속보도 부족·판단 불가 5가지로만 표시한다. "
        "후속보도가 확인되지 않은 채 ${days}일 이상 지난 사건은 책임기관 공식 게시판의 목록 제목만 확인했고, 언론사 원문은 수집하지 않았다."
    ),
    "title_event": Template("[사건 브리프] ${title} — ${status}"),
    "event_summary": Template("${occurred} ${region}에서 발생한 사건이다(${incident}). 책임기관은 ${org}${facility}."),
    "event_followup_intro": Template("발표 이후 기사 가운데 약속 행동과 관련된 후속보도 ${count}건이 확인됐다."),
    "event_followup_intro_nopromise": Template("사건 이후 기사 가운데 이 사건의 조치(복구·재개·처분)를 다룬 후속보도 ${count}건이 확인됐다."),
    "event_signal": Template("${pub} ${provider} 「${title}」: ‘${span}’ (${signal}, 신뢰도 ${conf}) [${ref}]"),
    "event_gap": Template("${as_of} 기준으로 발표 이후 조치를 다룬 후속보도는 확인되지 않았다. 이 사건에 연결된 마지막 기사는 ${last} 보도다."),
    "event_gap_none": Template("${as_of} 기준으로 발표 이후 조치를 다룬 후속보도는 확인되지 않았다."),
    "event_notice_found": Template("책임기관 공식 게시판에서 관련 발표 ${count}건이 확인됐다. 뉴스에는 확인되지 않았지만 기관은 발표한 내용일 수 있다."),
    "event_notice_found_neutral": Template("책임기관 공식 게시판에서도 관련 발표 ${count}건이 확인됐다(보조 근거)."),
    "event_notice_state": Template("기관 발표 확인: ${label}. ${reason}"),  # label·reason 은 notice_state_text/notice_reason_text 로 읽는 말
    "event_status": Template("미결의 현재 표시 상태는 ‘${status}’이다."),
}


@dataclass
class Draft:
    scope: str
    title: str
    sections: list[dict]
    sources: list[dict]
    generated_at: str
    event_id: int | None = None
    writer: str = "template-v1"
    disclaimer: str = DISCLAIMER
    header_note: str = HEADER_NOTE
    extra: dict = field(default_factory=dict)
    format: str = "brief"  # brief(사건 브리프) | article(기사체)
    subtitle: str | None = None  # 기사체만

    @property
    def markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        if self.format == "article":
            if self.subtitle:
                lines += [f"**{self.subtitle}**", ""]
            lines += [f"> {self.header_note}", ""]
            for s in self.sections:
                if s["heading"] == SOURCES_HEADING:
                    lines += [f"## {s['heading']}", ""]
                lines += [s["body"].rstrip(), ""]
            return "\n".join(lines).rstrip() + "\n"
        lines += [f"> {self.header_note}", ""]
        for s in self.sections:
            lines += [f"## {s['heading']}", "", s["body"].rstrip(), ""]
        return "\n".join(lines).rstrip() + "\n"

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "event_id": self.event_id,
            "format": self.format,
            "title": self.title,
            "subtitle": self.subtitle,
            "markdown": self.markdown,
            "sections": self.sections,
            "generated_at": self.generated_at,
            "sources": self.sources,
            "disclaimer": self.disclaimer,
            "header_note": self.header_note,
            "writer": self.writer,
            **self.extra,
        }


class PressWriter(Protocol):
    name: str

    def write_all(self, ov: D.Overview) -> Draft: ...

    def write_event(self, ev: D.EventFacts, as_of: date) -> Draft: ...


# ---------------------------------------------------------------------------
# 문장 도우미
# ---------------------------------------------------------------------------


def has_batchim(word: str) -> bool:
    for ch in reversed(word.strip()):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isdigit():
            return ch in "013678"
        if ch.isalpha():
            return ch.lower() in "lmnr"
    return False


def josa(word: str, with_b: str, without_b: str) -> str:
    return f"{word}{with_b if has_batchim(word) else without_b}"


def kdate(d: date | None) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일" if d else "날짜 미상"


def pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def source_article_count(exp: dict, ov: D.Overview) -> int:
    """원천 기사 수 = 인용문 검색으로 모은 기사 수(collected). 'unique' 는 사건마다 대표 원천 기사 1건이라 사건 수와 같다 — 대시보드 흐름줄과 같은 숫자를 쓴다."""
    sa = (exp or {}).get("source_articles") or {}
    return int(sa.get("collected") or sa.get("unique") or 0) if exp else ov.source_articles


def clip(text: str, n: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _now_iso() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


class _Sources:
    """근거 기사 번호표 — 처음 인용한 순서대로 [1], [2] …"""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self._idx: dict[str, int] = {}

    def ref(self, a: D.ArticleRef) -> int:
        if a.news_id not in self._idx:
            self.items.append(
                {
                    "ref": len(self.items) + 1,
                    "news_id": a.news_id,
                    "title": a.title,
                    "provider": a.provider,
                    "published_at": a.published_at.isoformat() if a.published_at else None,
                    "link": a.link,
                }
            )
            self._idx[a.news_id] = len(self.items)
        return self._idx[a.news_id]

    def section(self) -> dict:
        if not self.items:
            body = "인용한 근거 기사가 없다."
        else:
            body = "\n".join(
                f"[{s['ref']}] {s['provider']}, 「{s['title']}」, {s['published_at'] or '날짜 미상'}" + (f" — {s['link']}" if s["link"] else "")
                for s in self.items
            )
        return {"heading": SOURCES_HEADING, "body": body}


def _period(ov: D.Overview) -> str:
    dates = [e.occurred_at for e in ov.events]
    if not dates:
        return "분석 기간"
    lo, hi = min(dates), max(dates)
    return f"{kdate(lo)}~{kdate(hi)} 발생" if lo != hi else f"{kdate(lo)} 발생"


def _first_promise(ev: D.EventFacts) -> D.PromiseRef | None:
    firm = [p for p in ev.promises if p.strength == "확약"]
    return (firm or ev.promises or [None])[0]


# ---------------------------------------------------------------------------
# TemplateWriter
# ---------------------------------------------------------------------------


class TemplateWriter:
    name = "template-v1"
    format = "brief"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ---- 전체 초안 ----
    def write_all(self, ov: D.Overview) -> Draft:
        st = self.settings
        src = _Sources()
        exp = ov.experiment or {}
        exp_events = exp.get("events") or {}
        total = int(exp_events.get("total") or 0) if exp else ov.total
        followup = int(exp_events.get("with_followup") or 0) if exp else len(ov.with_followup)
        rate = (followup / total) if total else 0.0
        sources_n = source_article_count(exp, ov)
        as_of = kdate(date.fromisoformat(exp["as_of"])) if exp.get("as_of") else kdate(ov.as_of)

        sections: list[dict] = []
        lead = T["lead"].substitute(period=_period(ov), sources=f"{sources_n:,}", events=total, followup=followup, rate=pct(rate), rest=total - followup, as_of=as_of)
        if exp and (exp.get("source_articles") or {}).get("collected"):
            lead += " " + T["lead_exp"].substitute(events=total, generated=exp.get("generated_at", "")[:10])
        sections.append({"heading": "요약", "body": lead})

        inc = D.by_incident(ov)
        if inc:
            body = "\n".join(
                f"- {r['incident_type']}: 사건 {r['events']}건, 후속보도 확인 {r['with_followup']}건({pct(float(r.get('rate') or 0.0))})" for r in inc
            )
            sections.append({"heading": "재난 유형별 현황", "body": body})

        orgs = [r for r in D.by_org(ov) if (r.get("with_followup") or 0) > 0]
        if orgs:
            names = ", ".join(f"{r['org']}(사건 {r['events']}건 중 {r['with_followup']}건)" for r in orgs)
            body = T["orgs_intro"].substitute(count=len(orgs), names=names)
        else:
            body = T["orgs_none"].substitute(as_of=as_of)
        cases = D.followup_cases(ov, 3)
        if cases:
            body += "\n\n대표 사례\n"
            for i, ev in enumerate(cases, 1):
                s = ev.best_signal
                assert s is not None
                p = _first_promise(ev)
                if p is not None:
                    pref = f" [{src.ref(p.article)}]" if p.article is not None else ""
                    ref = src.ref(s.article)
                    line = T["case_followup"].substitute(
                        org_josa=josa(p.actor_org if p.actor_org != "기관 미상" else ev.org, "은", "는"),
                        spoken=kdate(p.spoken_at),
                        action=clip(p.action, 60),
                        rago=_rago(clip(p.action, 60)),
                        pub=kdate(s.article.published_at),
                        provider=s.article.provider,
                        title=clip(s.article.title, 60),
                        span=clip(s.span, 80),
                        signal=ev.status_signal_text,
                        ref=ref,
                        pref=pref,
                    )
                else:
                    ref = src.ref(s.article)
                    line = f"{kdate(s.article.published_at)} {s.article.provider} 보도 「{clip(s.article.title, 60)}」에서 ‘{clip(s.span, 80)}’ 내용이 확인됐다({ev.status_signal_text}) [{ref}]."
                body += f"\n{i}. {ev.title}: {line}"
        sections.append({"heading": "후속보도가 확인된 기관과 사례", "body": body})

        gaps = D.gap_cases(ov, 3)
        rest = total - followup
        body = T["gap_intro"].substitute(count=rest, as_of=as_of)
        if gaps:
            body += "\n\n대표 사례\n"
            for i, ev in enumerate(gaps, 1):
                body += f"\n{i}. {ev.title}: {self._gap_sentence(ev, src)}"
        sections.append({"heading": "후속보도가 확인되지 않은 사건", "body": body})

        ns = D.notice_summary(ov)
        if ns["triggered"] == 0:
            body = T["notice_none"].substitute(days=st.notice_trigger_days)
        else:
            parts = [
                f"{label} {ns[k]}건"
                for k, label in (("found", "기관 발표가 확인된 사건"), ("not_found", "게시판을 확인했으나 관련 글이 확인되지 않은 사건"), ("not_checked", "확인 대기 사건"), ("no_site", "등록된 게시판이 없는 사건"))
                if ns[k]
            ]
            body = T["notice_intro"].substitute(days=st.notice_trigger_days, triggered=ns["triggered"], breakdown=", ".join(parts) or "아직 없음")
            for ev in ns["found_events"]:
                for it in ev.notices.get("items", [])[:2]:
                    body += "\n- " + T["notice_item"].substitute(title=ev.title, org=it.get("site_name") or ev.org, notice=clip(it["title"], 60), date=it["published_at"] or "날짜 미상", url=it["url"])
        sections.append({"heading": "기관 공식 게시판 확인 결과", "body": body})

        sections.append({"heading": "분석 방법", "body": T["method"].substitute(min_conf=st.signal_min_conf, period=_period(ov), as_of=as_of, days=st.notice_trigger_days)})
        sections.append({"heading": "유의 사항", "body": f"{DISCLAIMER} {NOT_JUDGMENT}"})
        sections.append({"heading": "문의", "body": CONTACT})
        sections.append(src.section())
        return Draft(
            scope="all",
            title=T["title_all"].substitute(events=total, followup=followup),
            sections=sections,
            sources=src.items,
            generated_at=_now_iso(),
            writer=self.name,
            extra={
                "stats": {"source_articles": sources_n, "events": total, "with_followup": followup, "followup_rate": round(rate, 3), "as_of": ov.as_of.isoformat()},
                "data_source": "experiment" if exp else "db",
                "experiment_generated_at": exp.get("generated_at") if exp else None,
            },
        )

    def _gap_sentence(self, ev: D.EventFacts, src: _Sources) -> str:
        p = _first_promise(ev)
        if p is None or p.article is None:
            return T["case_gap_nopromise"].substitute(occurred=kdate(ev.occurred_at))
        since = kdate(ev.last_followup_at) + " 보도" if ev.last_followup_at else kdate(p.spoken_at) + " 발언"
        return T["case_gap"].substitute(
            org_josa=josa(p.actor_org if p.actor_org != "기관 미상" else ev.org, "은", "는"),
            spoken=kdate(p.spoken_at),
            action=clip(p.action, 60),
            rago=_rago(clip(p.action, 60)),
            ref=src.ref(p.article),
            since=since,
        )

    # ---- 사건별 초안 ----
    def write_event(self, ev: D.EventFacts, as_of: date) -> Draft:
        src = _Sources()
        sections: list[dict] = []
        summary = T["event_summary"].substitute(
            occurred=kdate(ev.occurred_at),
            region=ev.region or "지역 미상",
            incident=ev.incident_type or "분류 없음",
            org=ev.org or "미상",
            facility=f"이고 관련 시설은 {ev.facility}이다" if ev.facility else "이다",
        )
        if ev.summary:
            summary += f"\n\n사건 개요(추적 시작 때 등록한 설명으로, 근거 기사 번호가 붙지 않는다): {clip(ev.summary, 300)}"
        sections.append({"heading": "사건 요약", "body": summary})

        if ev.promises:
            lines = []
            for p in ev.promises:
                ref = f" [{src.ref(p.article)}]" if p.article else ""
                who = _promise_name(p, ev)  # 발언 주체는 기관 단위 (§2.4) — 원문 화자는 인용 출처(근거 기사)로만
                deadline = f", 기한 표현 ‘{p.deadline_raw}’" if p.deadline_raw else ""
                lines.append(f"- {kdate(p.spoken_at)} {who}: “{clip(p.action, 80)}” ({p.strength}{deadline}){ref}")
            body = "\n".join(lines)
        else:
            body = "공공기관의 추적 가능한 대응 발언이 확인되지 않았다."
        sections.append({"heading": "발표된 약속", "body": body})

        if ev.signals:
            lines = [(T["event_followup_intro"] if ev.promises else T["event_followup_intro_nopromise"]).substitute(count=len(ev.signals))]
            for s in sorted(ev.signals, key=lambda s: s.article.published_at or date.min):
                lines.append(
                    "- "
                    + T["event_signal"].substitute(
                        pub=kdate(s.article.published_at),
                        provider=s.article.provider,
                        title=clip(s.article.title, 60),
                        span=clip(s.span, 80),
                        signal=SIGNAL_TEXT.get(s.signal, s.signal),
                        conf=f"{s.confidence:.2f}",
                        ref=src.ref(s.article),
                    )
                )
            body = "\n".join(lines)
        else:
            body = (T["event_gap"].substitute(as_of=kdate(as_of), last=kdate(ev.last_followup_at)) if ev.last_followup_at else T["event_gap_none"].substitute(as_of=kdate(as_of)))
            body += f" {DISCLAIMER}"
        sections.append({"heading": "후속보도", "body": body})

        n = ev.notices or {}
        if n.get("items"):
            lines = [(T["event_notice_found"] if n.get("news_gap") else T["event_notice_found_neutral"]).substitute(count=len(n["items"]))]
            for it in n["items"]:
                lines.append(f"- 「{clip(it['title'], 80)}」 ({it['published_at'] or '날짜 미상'}, {it.get('site_name') or ev.org}) — {it['url']}")
            body = "\n".join(lines)
        else:
            body = T["event_notice_state"].substitute(label=notice_state_text(n), reason=notice_reason_text(n))
        sections.append({"heading": "기관 발표 확인", "body": body})

        status = ev.status if ev.judged else "판정 전"
        sections.append({"heading": "현재 상태", "body": T["event_status"].substitute(status=status) + f" {DISCLAIMER} {NOT_JUDGMENT}"})
        sections.append({"heading": "문의", "body": CONTACT})
        sections.append(src.section())
        return Draft(
            scope="event",
            event_id=ev.id,
            title=T["title_event"].substitute(title=ev.title, status=status),
            sections=sections,
            sources=src.items,
            generated_at=_now_iso(),
            writer=self.name,
            extra={"stats": {"promises": len(ev.promises), "followup_signals": len(ev.signals), "notice_items": len(n.get("items") or []), "as_of": as_of.isoformat()}},
        )


# ---------------------------------------------------------------------------
# ArticleWriter — 「기사체」. 숫자·날짜·근거 번호는 전부 data.py 집계에서 오고, 인용 순서는 TemplateWriter 와 같다(같은 [n]).
# ---------------------------------------------------------------------------

A = {
    "title_all": Template("재난·안전 사건 ${events}건 중 ${followup}건, 기관 발표 뒤 후속보도 확인됐다"),
    "subtitle_all": Template("나머지 ${rest}건은 ${as_of} 기준 후속보도 확인되지 않아… 뉴스 애프터케어 서비스 ‘미결’ 분석"),
    "lead_all": Template(
        "팀 후속취재단이 운영하는 뉴스 애프터케어 서비스 ‘미결’이 한국언론진흥재단 빅카인즈 API로 ${period} 재난·안전 사건 ${events}건을 추적한 결과, "
        "${as_of} 기준으로 ${followup}건(${rate})에서 공공기관의 대응 발표 이후 조치를 다룬 후속보도가 확인됐다. "
        "나머지 ${rest}건은 같은 날짜 기준으로 후속보도가 확인되지 않았다."
    ),
    "lead_all_sources": Template("분석 대상은 사건 발생 뒤 공공기관의 대응 발언이 담긴 원천 기사 ${sources}건이다."),
    "lead_all_exp": Template("빅카인즈 인용문 검색으로 모은 이 기사들을 같은 사건끼리 묶어 사건 ${events}건으로 정리했다(실험 리포트 ${generated} 기준)."),
    "incident": Template("재난 유형별로는 ${items}에서 후속보도가 확인됐다."),
    "incident_item": Template("‘${incident_type}’ 사건 ${events}건 가운데 ${with_followup}건(${rate})"),
    "orgs": Template("기관별로 보면 후속보도가 확인된 기관은 모두 ${count}곳으로, ${names}${etc}이다."),
    "orgs_none": Template("${as_of} 기준으로 후속보도가 확인된 기관은 없다."),
    "case_open": (
        Template("대표적인 사례가 ‘${title}’ 사건이다."),
        Template("‘${title}’ 사건에서도 후속보도가 확인됐다."),
        Template("‘${title}’ 사건 역시 마찬가지다."),
    ),
    "case_followup": Template("${org_josa} ${spoken} “${action}”${rago} 밝혔고${pref}, 이후 ${pub} ${provider} 보도 「${title}」에서 ‘${span}’ 내용이 확인됐다(${signal}) [${ref}]."),
    "case_followup_nopromise": Template("${pub} ${provider} 보도 「${title}」에서 ‘${span}’ 내용이 확인됐다(${signal}) [${ref}]."),
    "gap_intro": Template("반면 후속보도가 확인되지 않은 사건은 ${count}건이다. 이는 발표 이후의 조치를 다룬 보도가 ${as_of} 기준으로 확인되지 않았다는 뜻이며, 조치가 없었다는 뜻은 아니다."),
    "case_gap": Template("‘${title}’ 사건의 경우 ${org_josa} ${spoken} “${action}”${rago} 밝혔으나 [${ref}], ${since} 이후 이 약속과 관련된 후속보도는 확인되지 않았다."),
    "case_gap_nopromise": Template("‘${title}’ 사건은 ${occurred} 발생 이후 공공기관의 구체적인 대응 발언과 후속보도가 확인되지 않았다."),
    "notice_none": Template("후속보도가 확인되지 않은 채 마지막 보도·약속 이후 ${days}일 이상 지난 사건은 없어, 기관 공식 게시판 확인 대상은 없었다."),
    "notice_intro": Template(
        "미결은 후속보도가 확인되지 않은 채 마지막 보도·약속 이후 ${days}일 이상 지난 사건 ${triggered}건에 대해 책임기관의 공식 보도자료·공지사항 게시판을 확인했다. "
        "그 결과 ${breakdown}이었다."
    ),
    "notice_item": Template("‘${title}’ 사건은 ${org} 게시판에 ${date} 「${notice}」 제목의 발표가 올라와 있는 것이 확인됐다."),
    "closing_all": Template("이번 분석의 기준일은 ${as_of}이다. ${disclaimer} ${not_judgment} 사례 문장에는 근거 기사 번호를 달았고 집계 숫자는 실험 리포트와 데이터베이스 집계에서 가져왔으며, 배포 전 사람이 검토한다."),
    # ---- 사건별 ----
    "title_event_followup": Template("‘${title}’, 기관 발표 이후 후속보도 ${count}건 확인"),
    "title_event_followup_nopromise": Template("‘${title}’, 사건 이후 조치를 다룬 후속보도 ${count}건 확인"),
    "title_event_gap": Template("‘${title}’, 기관 발표 이후 후속보도 확인되지 않아"),
    "title_event_gap_notice": Template("‘${title}’, 뉴스 후속보도는 확인되지 않았지만 기관 게시판 발표 ${count}건 확인"),
    "title_event_nopromise": Template("‘${title}’, 공공기관 대응 발언·후속보도 확인되지 않아"),
    "subtitle_event": Template("${occurred} ${region} 발생 · 책임기관 ${org} · 미결 표시 상태 ‘${status}’(${as_of} 기준)"),
    "lead_event_followup": Template(
        "${occurred} ${region}에서 발생한 ‘${title}’(${incident}) 사건과 관련해 ${promises_clause}"
        "${as_of} 기준으로 발표 이후 조치를 다룬 후속보도 ${count}건이 확인됐다고 뉴스 애프터케어 서비스 ‘미결’이 밝혔다."
    ),
    "lead_promises_some": Template("공공기관의 대응 발언 ${promises}건이 확인됐고, "),
    "lead_promises_none": "공공기관의 추적 가능한 대응 발언은 확인되지 않았으나, ",
    "lead_event_gap": Template(
        "${occurred} ${region}에서 발생한 ‘${title}’(${incident}) 사건과 관련해 공공기관의 대응 발언 ${promises}건이 확인됐으나, "
        "${as_of} 기준으로 발표 이후 조치를 다룬 후속보도는 확인되지 않았다고 뉴스 애프터케어 서비스 ‘미결’이 밝혔다."
    ),
    "lead_event_nopromise": Template(
        "${occurred} ${region}에서 발생한 ‘${title}’(${incident}) 사건과 관련해 ${as_of} 기준으로 공공기관의 추적 가능한 대응 발언과 후속보도가 확인되지 않았다고 "
        "뉴스 애프터케어 서비스 ‘미결’이 밝혔다."
    ),
    "lead_event_facility": Template("책임기관은 ${org}이고 관련 시설은 ${facility}이다."),
    "lead_event_org": Template("책임기관은 ${org}이다."),
    "promise_first": Template("${who_josa} ${spoken} “${action}”${rago} 밝혔다${note} [${ref}]."),
    "promise_next": (
        Template("이어 ${spoken}에는 ${who_ga} “${action}”${rago} 말했다${note} [${ref}]."),
        Template("${spoken} ${who_josa} “${action}”${rago} 밝혔다${note} [${ref}]."),
        Template("${who}도 ${spoken} “${action}”${rago} 전했다${note} [${ref}]."),
    ),
    "promise_noref": Template("${who_josa} ${spoken} “${action}”${rago} 밝혔다${note}."),
    "promise_none": Template("${as_of} 기준으로 공공기관의 추적 가능한 대응 발언은 확인되지 않았다."),
    "signal_intro": Template("발표 이후 기사 가운데 약속 행동과 관련된 후속보도는 ${count}건이 확인됐다."),
    "signal_intro_nopromise": Template("사건 이후 기사 가운데 이 사건의 조치(복구·재개·처분)를 다룬 후속보도는 ${count}건이 확인됐다."),
    "signal_item": Template("${pub} ${provider} 보도 「${title}」에서는 ‘${span}’ 내용이 확인됐다(${signal}, 신뢰도 ${conf}) [${ref}]."),
    "signal_gap": Template("${as_of} 기준으로 발표 이후 조치를 다룬 후속보도는 확인되지 않았다. 이 사건에 연결된 마지막 기사는 ${last} 보도다."),
    "signal_gap_none": Template("${as_of} 기준으로 발표 이후 조치를 다룬 후속보도는 확인되지 않았다."),
    "event_notice_intro": Template("한편 책임기관의 공식 게시판에서는 관련 발표 ${count}건이 확인됐다. 뉴스에는 확인되지 않았지만 기관이 발표한 내용일 수 있다."),
    "event_notice_intro_neutral": Template("한편 책임기관의 공식 게시판에서도 관련 발표 ${count}건이 확인됐다(보조 근거)."),
    "event_notice_site": Template("${site} 게시판에는 ${items}${etc} 올라와 있다."),  # etc = ' 등이' | josa(마지막 제목, '이', '가')
    "event_notice_tail": "게시판 주소와 전체 목록은 사건 브리프에 있다.",
    "event_notice_state": Template("책임기관 공식 게시판 확인 결과: ${label}. ${reason}"),
    "background_event": Template(
        "미결은 사건 발생 후 공공기관이 대응을 밝힌 인용문을 원천 기사로 삼아 같은 사건 기사를 규칙과 문장 유사도로 묶고, "
        "발표 이후 기사에서 약속 행동과 겹치는 완료·진행·새로운 문제 표현(신뢰도 ${min_conf} 이상)을 찾아 사건 상태를 정하고, 상태가 조치 완료 근거 확인·조치 진행 정황 확인·새로운 문제 발생인 사건을 ‘후속보도 확인’으로 센다. "
        "사건 상태는 조치 완료 근거 확인·조치 진행 정황 확인·새로운 문제 발생·후속보도 부족·판단 불가 5가지로만 표시하며, 이 사건의 현재 표시 상태는 ‘${status}’이다. "
        "대응 발언은 표현의 강도에 따라 ‘확약’과 ‘계획’으로 나눠 추적했다. "
        "후속보도가 확인되지 않은 채 ${days}일 이상 지난 사건은 책임기관 공식 게시판의 목록 제목만 확인하고 언론사 원문은 수집하지 않는다."
    ),
    "closing_event": Template("이 초안의 기준일은 ${as_of}이다. ${disclaimer} ${not_judgment}"),
}

ARTICLE_LEAD = "리드"
ARTICLE_BODY = "본문"
ARTICLE_BACKGROUND = "배경"
ARTICLE_CLOSING = "마무리"


NOTICE_STATE_TEXT = {
    "not_triggered": "확인 대상 아님",
    "no_site": "책임기관의 게시판이 등록부에 없음",
    "not_checked": "확인 대기",
    "not_found": "게시판을 확인했으나 관련 글 없음",
    "found": "기관 발표 확인",
    "error": "게시판 접속 오류",
}


def notice_state_text(n: dict) -> str:
    """내부 상태값을 독자가 읽는 말로 ('트리거 조건 미충족' 같은 내부 용어를 초안에 쓰지 않는다 — 10-30 press-10)."""
    return NOTICE_STATE_TEXT.get(str(n.get("state") or ""), n.get("state_label") or "아직 확인 전")


def notice_reason_text(n: dict) -> str:
    """트리거 사유 문장을 초안용으로 — 상태가 후속보도 확인이면 '후속보도가 확인된 사건이라 게시판을 확인하지 않았다' 로, 기간 미달이면 경과일만."""
    reason = str(n.get("reason") or "")
    if n.get("state") == "not_triggered":
        if "상태가" in reason:
            return "후속보도가 확인됐거나 아직 판정 전인 사건은 기관 게시판을 확인하지 않는다."
        if "경과" in reason:
            return "마지막 보도·약속 이후 기준 기간이 지나지 않아 아직 확인하지 않았다."
        return ""
    return reason.replace("트리거", "확인 조건") if reason else ""


def _paragraphs(*paras: str) -> str:
    return "\n\n".join(p.strip() for p in paras if p and p.strip())


def _sentences(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def _promise_name(p: D.PromiseRef, ev: D.EventFacts) -> str:
    return p.actor_org if p.actor_org != "기관 미상" else ev.org


def _rago(quoted: str) -> str:
    """닫는 따옴표 앞 글자에 받침이 있으면 ‘이라고’, 없으면 ‘라고’."""
    return "이라고" if has_batchim(quoted) else "라고"


def _promise_note(p: D.PromiseRef) -> str:
    """확약이거나 기한 표현이 있을 때만 괄호 설명. ‘계획’ 단계 발언은 배경 문단에서 한 번만 설명한다."""
    parts = []
    if p.strength != "계획":
        parts.append(p.strength)
    if p.deadline_raw:
        parts.append(f"기한 표현 ‘{p.deadline_raw}’")
    return f"({', '.join(parts)})" if parts else ""


class ArticleWriter:
    """기사체 작성기 — TemplateWriter 와 같은 집계·같은 인용 순서로 제목·부제·리드·본문·배경·마무리 문단을 쓴다."""

    name = "article-v1"
    format = "article"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ---- 전체 현황 ----
    def write_all(self, ov: D.Overview) -> Draft:
        st = self.settings
        src = _Sources()
        exp = ov.experiment or {}
        exp_events = exp.get("events") or {}
        total = int(exp_events.get("total") or 0) if exp else ov.total
        followup = int(exp_events.get("with_followup") or 0) if exp else len(ov.with_followup)
        rate = (followup / total) if total else 0.0
        sources_n = source_article_count(exp, ov)
        as_of = kdate(date.fromisoformat(exp["as_of"])) if exp.get("as_of") else kdate(ov.as_of)
        rest = total - followup

        # 리드
        lead_src = A["lead_all_sources"].substitute(sources=f"{sources_n:,}")
        if exp and (exp.get("source_articles") or {}).get("collected"):
            lead_src = _sentences(lead_src, A["lead_all_exp"].substitute(events=total, generated=exp.get("generated_at", "")[:10]))
        lead = _paragraphs(
            A["lead_all"].substitute(period=_period(ov), events=total, as_of=as_of, followup=followup, rate=pct(rate), rest=rest),
            lead_src,
        )

        # 본문
        body_paras: list[str] = []
        inc = D.by_incident(ov)
        if inc:
            items = ", ".join(A["incident_item"].substitute(incident_type=r["incident_type"], events=r["events"], with_followup=r["with_followup"], rate=pct(float(r.get("rate") or 0.0))) for r in inc)
            body_paras.append(A["incident"].substitute(items=items))
        orgs = [r for r in D.by_org(ov) if (r.get("with_followup") or 0) > 0]
        if orgs:
            shown = orgs[:5]
            names = ", ".join(f"{r['org']}(사건 {r['events']}건 중 {r['with_followup']}건)" for r in shown)
            body_paras.append(A["orgs"].substitute(count=len(orgs), names=names, etc=" 등" if len(orgs) > len(shown) else ""))
        else:
            body_paras.append(A["orgs_none"].substitute(as_of=as_of))

        for i, ev in enumerate(D.followup_cases(ov, 3)):
            s = ev.best_signal
            assert s is not None
            p = _first_promise(ev)
            opener = A["case_open"][min(i, len(A["case_open"]) - 1)].substitute(title=ev.title)
            if p is not None:
                pref = f" [{src.ref(p.article)}]" if p.article is not None else ""
                line = A["case_followup"].substitute(
                    org_josa=josa(_promise_name(p, ev), "은", "는"),
                    spoken=kdate(p.spoken_at),
                    action=clip(p.action, 60),
                    rago=_rago(clip(p.action, 60)),
                    pref=pref,
                    pub=kdate(s.article.published_at),
                    provider=s.article.provider,
                    title=clip(s.article.title, 60),
                    span=clip(s.span, 80),
                    signal=ev.status_signal_text,
                    ref=src.ref(s.article),
                )
            else:
                line = A["case_followup_nopromise"].substitute(
                    pub=kdate(s.article.published_at), provider=s.article.provider, title=clip(s.article.title, 60), span=clip(s.span, 80), signal=ev.status_signal_text, ref=src.ref(s.article)
                )
            body_paras.append(_sentences(opener, line))

        gap_lines = [A["gap_intro"].substitute(count=rest, as_of=as_of)]
        for ev in D.gap_cases(ov, 3):
            gap_lines.append(self._gap_sentence(ev, src))
        body_paras.append(_sentences(*gap_lines))

        ns = D.notice_summary(ov)
        if ns["triggered"] == 0:
            body_paras.append(A["notice_none"].substitute(days=st.notice_trigger_days))
        else:
            parts = [
                f"{label} {ns[k]}건"
                for k, label in (("found", "기관 발표가 확인된 사건"), ("not_found", "게시판을 확인했으나 관련 글이 확인되지 않은 사건"), ("not_checked", "확인 대기 사건"), ("no_site", "등록된 게시판이 없는 사건"))
                if ns[k]
            ]
            lines = [A["notice_intro"].substitute(days=st.notice_trigger_days, triggered=ns["triggered"], breakdown=", ".join(parts) or "아직 없음")]
            for ev in ns["found_events"]:
                for it in ev.notices.get("items", [])[:1]:
                    lines.append(A["notice_item"].substitute(title=ev.title, org=it.get("site_name") or ev.org, notice=clip(it["title"], 60), date=it["published_at"] or "날짜 미상"))
            body_paras.append(_sentences(*lines))

        sections = [
            {"heading": ARTICLE_LEAD, "body": lead},
            {"heading": ARTICLE_BODY, "body": _paragraphs(*body_paras)},
            {"heading": ARTICLE_BACKGROUND, "body": T["method"].substitute(min_conf=st.signal_min_conf, period=_period(ov), as_of=as_of, days=st.notice_trigger_days)},
            {"heading": ARTICLE_CLOSING, "body": _paragraphs(A["closing_all"].substitute(as_of=as_of, disclaimer=DISCLAIMER, not_judgment=NOT_JUDGMENT), f"문의: {CONTACT}")},
            src.section(),
        ]
        return Draft(
            scope="all",
            title=A["title_all"].substitute(events=total, followup=followup),
            subtitle=A["subtitle_all"].substitute(rest=rest, as_of=as_of),
            format=self.format,
            sections=sections,
            sources=src.items,
            generated_at=_now_iso(),
            writer=self.name,
            extra={
                "stats": {"source_articles": sources_n, "events": total, "with_followup": followup, "followup_rate": round(rate, 3), "as_of": ov.as_of.isoformat()},
                "data_source": "experiment" if exp else "db",
                "experiment_generated_at": exp.get("generated_at") if exp else None,
            },
        )

    def _gap_sentence(self, ev: D.EventFacts, src: _Sources) -> str:
        p = _first_promise(ev)
        if p is None or p.article is None:
            return A["case_gap_nopromise"].substitute(title=ev.title, occurred=kdate(ev.occurred_at))
        since = kdate(ev.last_followup_at) + " 보도" if ev.last_followup_at else kdate(p.spoken_at) + " 발언"
        action = clip(p.action, 60)
        return A["case_gap"].substitute(title=ev.title, org_josa=josa(_promise_name(p, ev), "은", "는"), spoken=kdate(p.spoken_at), action=action, rago=_rago(action), ref=src.ref(p.article), since=since)

    # ---- 사건별 ----
    def write_event(self, ev: D.EventFacts, as_of: date) -> Draft:
        st = self.settings
        src = _Sources()
        n = ev.notices or {}
        items = n.get("items") or []
        status = ev.status if ev.judged else "판정 전"
        as_of_k = kdate(as_of)
        common = {"title": ev.title, "occurred": kdate(ev.occurred_at), "region": ev.region or "지역 미상", "incident": ev.incident_type or "분류 없음", "as_of": as_of_k}

        if ev.signals:
            title = (A["title_event_followup"] if ev.promises else A["title_event_followup_nopromise"]).substitute(title=ev.title, count=len(ev.signals))
            promises_clause = A["lead_promises_some"].substitute(promises=len(ev.promises)) if ev.promises else A["lead_promises_none"]
            lead = A["lead_event_followup"].substitute(**common, promises_clause=promises_clause, count=len(ev.signals))
        elif ev.promises:
            # '뉴스 후속보도는 확인되지 않았지만 기관 발표' 는 news_gap(발표 이후·같은 발표 아님·관련성)일 때만 (10-30 A2)
            title = A["title_event_gap_notice"].substitute(title=ev.title, count=n.get("news_gap_items") or len(items)) if items and n.get("news_gap") else A["title_event_gap"].substitute(title=ev.title)
            lead = A["lead_event_gap"].substitute(**common, promises=len(ev.promises))
        else:
            title = A["title_event_nopromise"].substitute(title=ev.title)
            lead = A["lead_event_nopromise"].substitute(**common)
        org_line = A["lead_event_facility"].substitute(org=ev.org or "미상", facility=ev.facility) if ev.facility else A["lead_event_org"].substitute(org=ev.org or "미상")
        lead = _paragraphs(_sentences(lead, org_line), ("사건 개요(추적 시작 때 등록한 설명): " + clip(ev.summary, 300)) if ev.summary else "")

        body_paras: list[str] = []
        if ev.promises:
            lines = []
            prev_spoken: date | None = None
            for i, p in enumerate(ev.promises):
                who = _promise_name(p, ev)  # 기관 단위 (§2.4). 원문 화자는 근거 기사로만
                action = clip(p.action, 80)
                spoken = "같은 날" if (i > 0 and p.spoken_at is not None and p.spoken_at == prev_spoken) else kdate(p.spoken_at)
                prev_spoken = p.spoken_at
                kw = {"who": who, "who_josa": josa(who, "은", "는"), "who_ga": josa(who, "이", "가"), "spoken": spoken, "action": action, "rago": _rago(action), "note": _promise_note(p)}
                if p.article is None:
                    lines.append(A["promise_noref"].substitute(**kw))
                elif i == 0:
                    lines.append(A["promise_first"].substitute(**kw, ref=src.ref(p.article)))
                else:
                    lines.append(A["promise_next"][(i - 1) % len(A["promise_next"])].substitute(**kw, ref=src.ref(p.article)))
            for k in range(0, len(lines), 3):
                body_paras.append(_sentences(*lines[k : k + 3]))
        else:
            body_paras.append(A["promise_none"].substitute(as_of=as_of_k))

        if ev.signals:
            lines = [(A["signal_intro"] if ev.promises else A["signal_intro_nopromise"]).substitute(count=len(ev.signals))]
            for s in sorted(ev.signals, key=lambda s: s.article.published_at or date.min):
                lines.append(
                    A["signal_item"].substitute(
                        pub=kdate(s.article.published_at),
                        provider=s.article.provider,
                        title=clip(s.article.title, 60),
                        span=clip(s.span, 80),
                        signal=SIGNAL_TEXT.get(s.signal, s.signal),
                        conf=f"{s.confidence:.2f}",
                        ref=src.ref(s.article),
                    )
                )
            body_paras.append(_sentences(*lines))
        else:
            gap = A["signal_gap"].substitute(as_of=as_of_k, last=kdate(ev.last_followup_at)) if ev.last_followup_at else A["signal_gap_none"].substitute(as_of=as_of_k)
            body_paras.append(_sentences(gap, DISCLAIMER))

        if items:
            lines = [(A["event_notice_intro"] if n.get("news_gap") else A["event_notice_intro_neutral"]).substitute(count=len(items))]
            by_site: dict[str, list[dict]] = {}
            for it in items:
                by_site.setdefault(it.get("site_name") or ev.org, []).append(it)
            for site, its in by_site.items():
                shown = its[:3]
                last_title = clip(shown[-1]["title"], 60)
                lines.append(A["event_notice_site"].substitute(site=site, items=", ".join(f"{it['published_at'] or '날짜 미상'} 「{clip(it['title'], 60)}」" for it in shown), etc=" 등이" if len(its) > len(shown) else ("이" if has_batchim(last_title) else "가")))
            lines.append(A["event_notice_tail"])
            body_paras.append(_sentences(*lines))
        else:
            body_paras.append(A["event_notice_state"].substitute(label=notice_state_text(n), reason=notice_reason_text(n)))

        sections = [
            {"heading": ARTICLE_LEAD, "body": lead},
            {"heading": ARTICLE_BODY, "body": _paragraphs(*body_paras)},
            {"heading": ARTICLE_BACKGROUND, "body": A["background_event"].substitute(min_conf=st.signal_min_conf, status=status, days=st.notice_trigger_days)},
            {"heading": ARTICLE_CLOSING, "body": _paragraphs(A["closing_event"].substitute(as_of=as_of_k, disclaimer=DISCLAIMER, not_judgment=NOT_JUDGMENT), f"문의: {CONTACT}")},
            src.section(),
        ]
        return Draft(
            scope="event",
            event_id=ev.id,
            title=title,
            subtitle=A["subtitle_event"].substitute(occurred=kdate(ev.occurred_at), region=ev.region or "지역 미상", org=ev.org or "미상", status=status, as_of=as_of_k),
            format=self.format,
            sections=sections,
            sources=src.items,
            generated_at=_now_iso(),
            writer=self.name,
            extra={"stats": {"promises": len(ev.promises), "followup_signals": len(ev.signals), "notice_items": len(items), "as_of": as_of.isoformat()}},
        )


# ---------------------------------------------------------------------------
# LLMWriter (LLM_MODE=live 일 때만)
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"\[\d+\]")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_FORBIDDEN = ("미" + "이행", "불" + "이행", "거짓" + "말")
POLISH_HEADINGS = {"요약", "후속보도가 확인된 기관과 사례", "후속보도가 확인되지 않은 사건", "사건 요약", "후속보도", ARTICLE_LEAD, ARTICLE_BODY}


def polish_is_safe(before: str, after: str) -> bool:
    """다듬은 문장이 숫자·근거 번호를 그대로 두고 금지 표현이 없을 때만 채택."""
    if not after.strip() or any(w in after for w in _FORBIDDEN):
        return False
    if sorted(_REF_RE.findall(before)) != sorted(_REF_RE.findall(after)):
        return False
    return sorted(_NUM_RE.findall(before)) == sorted(_NUM_RE.findall(after))


class LLMWriter:
    """TemplateWriter(브리프)·ArticleWriter(기사체) 초안의 일부 절 문장만 다듬는다. 실패·검증 탈락은 템플릿 문장 유지."""

    name = "llm-press-v1"

    def __init__(self, settings: Settings, client=None, base: PressWriter | None = None) -> None:
        from ..llm.client import load_prompt

        self.settings = settings
        self.template = base or TemplateWriter(settings)
        self.format = getattr(self.template, "format", "brief")
        self.system = load_prompt("press_v1")
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else anthropic.Anthropic()
        self.client = client

    def _polish(self, text: str) -> str:
        try:
            resp = self.client.messages.create(
                model=self.settings.llm_model,
                max_tokens=4000,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": text}],
            )
            out = "".join(getattr(b, "text", "") for b in resp.content).strip()
        except Exception as exc:  # noqa: BLE001 — 다듬기는 선택 단계: 실패하면 템플릿 문장
            log.warning("보도자료 문장 다듬기 실패(템플릿 유지): %s", type(exc).__name__)
            return text
        return out if polish_is_safe(text, out) else text

    def _apply(self, draft: Draft) -> Draft:
        for s in draft.sections:
            if s["heading"] in POLISH_HEADINGS:
                s["body"] = self._polish(s["body"])
        draft.writer = f"{self.name}({self.settings.llm_model})"
        return draft

    def write_all(self, ov: D.Overview) -> Draft:
        return self._apply(self.template.write_all(ov))

    def write_event(self, ev: D.EventFacts, as_of: date) -> Draft:
        return self._apply(self.template.write_event(ev, as_of))


def build_writer(settings: Settings, fmt: str = "brief") -> PressWriter:
    """fmt: brief(사건 브리프, 기본) | article(기사체). LLM_MODE=live 면 두 형식 모두 LLMWriter 가 같은 검사(polish_is_safe)로 다듬는다."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown press format: {fmt}")
    base: PressWriter = ArticleWriter(settings) if fmt == "article" else TemplateWriter(settings)
    if settings.llm_mode == "live":
        return LLMWriter(settings, base=base)
    return base
