"""§4.2-3·4 유니크화와 자동 시드 — 원천 인용문을 사건 단위로 묶고 seeds/experiment.draft.yaml 을 쓴다.

같은 사건 판정(유니크화): 발생일(인용 기사일) ±14일 안에서
  - 시설명이 같다, 또는
  - 기초 지역이 같고 재난 계통(물·붕괴·화재·교통 …)이 같다, 또는
  - 정규화 기관이 같고 재난 계통이 같으며 지역이 겹치거나 둘 다 없다, 또는
  - 같은 발언(전재)이다.
대표는 가장 이른 기사. 사건 후보는 '실제로 일어난 사고 뒤의 대응' 일수록 앞에 둔다(예방·계절 대비 기사는 뒤로).
선택은 (계열, 연도) 층을 돌아가며 점수 높은 순으로 채운다 — 한 계열·한 해에 몰리지 않게.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..rules.actors import normalize_actor_org
from ..rules.orgs import _CENTRAL, canonical_org, is_department_only, org_aliases
from ..rules.regions import ADMIN_NAMES, WIDE
from .sources import DiscoverResult, IssueEvent, SourceQuote
from .text import find_facility, is_preventive, looks_occurred, scrub

WINDOW_DAYS = 14
_FAMILIES: dict[str, tuple[str, ...]] = {
    "water": ("침수", "호우", "폭우", "홍수", "범람", "유실", "태풍", "산사태", "물난리", "수해"),
    "collapse": ("붕괴", "무너", "붕락", "매몰", "싱크홀", "땅꺼짐", "지반침하", "낙석"),
    "fire": ("화재", "불길", "화마", "폭발", "누출", "유출", "가스", "산불"),
    "traffic": ("추돌", "충돌", "탈선", "전복", "침몰", "좌초", "역주행", "급발진", "결빙", "블랙아이스"),
    "work": ("추락", "끼임", "질식", "감전", "중대재해", "산재"),
    "weather": ("폭염", "한파", "폭설", "대설", "강풍", "지진"),
}
_GENERIC_HAZARD = {"사고", "사망", "숨져", "숨진", "부상", "사상자", "희생", "실종", "참사", "피해"}
_TITLE_STOP = {
    "관계자", "정부", "대책", "점검", "추진", "발표", "마련", "강화", "지원", "논란", "의혹", "책임", "이번", "오늘", "내일", "어제", "올해", "지난해",
    "기자", "속보", "단독", "종합", "사진", "영상", "현장", "르포", "인터뷰", "사설", "칼럼", "시민", "주민", "유족", "유가족", "국민", "전국", "지역",
    "대비", "위험", "원인", "미래", "위기", "복구", "하루", "재난", "선포", "영업", "시간당", "수도권", "실종", "피해", "상황", "우려", "대응", "조사", "결과",
    "경찰", "소방", "대통령", "장관", "이후", "당시", "이재민", "특별", "관련", "위해", "위한", "비판", "계획", "확인", "발생", "주변", "인근", "특별재난지역",
    "사고", "참사", "사망", "부상", "숨져", "구조", "수색", "긴급", "총력", "만전", "철저", "당부", "지시", "주문", "필요", "개선", "문제", "점검", "강화",
}
# 계열 세부 분류 → 질의용 재난어 (제목에 고유 재난어가 없을 때)
_DETAIL_KEYWORD = {
    "붕괴": "붕괴", "폭발": "폭발", "화재": "화재", "원자력사고": "원전", "항공사고": "항공기", "해상사고": "선박", "철도사고": "열차", "노상사고": "교통사고",
    "가뭄": "가뭄", "지진": "지진", "홍수": "침수", "눈사태_산사태": "산사태", "태풍": "태풍", "폭염": "폭염", "해일": "해일",
}
_GENERIC_GOV = {"구청": "구", "시청": "시", "군청": "군", "도청": "도", "시": "시", "군": "군", "구": "구"}
_NOT_PUBLIC = {"항공사", "여행사", "건설사", "보험사", "통신사", "운송사", "제약사"}
_NOT_PUBLIC_RE = re.compile(r"(시민|유가족|유족|주민|노조|노총|협회|연대|진상|대책위|상인|피해자)")
_CENTRAL_KEYS = frozenset(_CENTRAL.values())
_PARTICLE_TAIL = re.compile(r"(에서는|에서|으로|에는|에게|까지|부터|에|의|은|는|이|가|을|를|과|와|도|로|서)$")
_WIDE_SET = frozenset(WIDE)


def hazard_family(words: list[str]) -> set[str]:
    out = set()
    for w in words:
        for fam, terms in _FAMILIES.items():
            if any(t in w for t in terms):
                out.add(fam)
    return out


def _basic_regions(q: SourceQuote) -> set[str]:
    return {r.rstrip("시군구") if len(r) > 2 else r for r in q.regions if r not in _WIDE_SET and not (r.endswith(("시", "도")) and r[:-1] in _WIDE_SET)}


def _same_event(a: SourceQuote, b: SourceQuote) -> bool:
    da, db = date.fromisoformat(a.published), date.fromisoformat(b.published)
    if abs((da - db).days) > WINDOW_DAYS:
        return False
    if a.quotation[:40] == b.quotation[:40] and a.source == b.source:
        return True  # 전재·같은 발언 반복
    if a.facility and a.facility == b.facility:
        return True
    fam = hazard_family(a.hazards) & hazard_family(b.hazards)
    ra, rb = _basic_regions(a), _basic_regions(b)
    if ra & rb and fam:
        return True
    if a.org and a.org == b.org and fam and (ra & rb or (not ra and not rb)):
        return True
    return False


@dataclass
class EventGroup:
    quotes: list[SourceQuote]
    issue_topic: str = ""
    issue_keywords: list[str] = field(default_factory=list)
    score: float = 0.0
    org: str = ""
    region: str = ""
    facility: str = ""
    incident_type: str = ""
    keywords: list[str] = field(default_factory=list)

    @property
    def rep(self) -> SourceQuote:
        strength_rank = {"확약": 0, "계획": 1}
        return min(self.quotes, key=lambda q: (q.published, strength_rank.get(q.strength, 2), q.news_id))

    @property
    def news_ids(self) -> list[str]:
        return list(dict.fromkeys(q.news_id for q in self.quotes))

    @property
    def occurred(self) -> date:
        return date.fromisoformat(self.rep.published)


def group_quotes(quotes: list[SourceQuote]) -> list[EventGroup]:
    qs = sorted(quotes, key=lambda q: q.published)
    parent = list(range(len(qs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(qs):
        da = date.fromisoformat(a.published)
        for j in range(i + 1, len(qs)):
            b = qs[j]
            if (date.fromisoformat(b.published) - da).days > WINDOW_DAYS:
                break
            if _same_event(a, b):
                parent[find(j)] = find(i)
    buckets: dict[int, list[SourceQuote]] = defaultdict(list)
    for i, q in enumerate(qs):
        buckets[find(i)].append(q)
    groups = [EventGroup(quotes=v) for v in buckets.values()]
    for g in groups:
        _describe(g)
    return groups


def _org_from_title_role(q: SourceQuote) -> str:
    """source 가 '이재호 구청장'·'박 서장' 처럼 기관 없이 직함만 있으면 제목의 지역·기관으로 보충한다."""
    src = q.source
    for tail, sfx in (("구청장", "구"), ("시장", "시"), ("군수", "군"), ("도지사", ""), ("지사", "")):
        if src.endswith(tail):
            for r in q.regions:
                key = canonical_org(r if not sfx or r.endswith(sfx) else r + sfx)
                if key and (not sfx or key.endswith(sfx)) and not (sfx and key in ("서울시", "부산시", "대구시", "인천시", "대전시", "울산시", "광주시", "세종시") and tail == "구청장"):
                    return key
    for tok in re.findall(r"[가-힣A-Za-z]{2,12}", q.title):
        key = canonical_org(tok)
        if key != tok or tok.endswith(("해양경찰서", "소방본부", "소방서", "경찰서")):
            if key and not is_department_only(key) and len(key) >= 2:
                return key
    return ""


def _describe(g: EventGroup) -> None:
    orgs = Counter()
    for q in g.quotes:
        o = q.org if q.org and len(q.org) >= 2 and not is_department_only(q.org) and q.org not in ("시", "군", "구", "도") else ""
        o = o or _org_from_title_role(q)
        if o:
            orgs[o] += 2 if q is g.rep else 1
    g.org = orgs.most_common(1)[0][0] if orgs else ""
    regions = Counter(r for q in g.quotes for r in q.regions[:2])
    basic = [r for r, _ in regions.most_common() if r not in _WIDE_SET and not (r.endswith(("시", "도")) and r[:-1] in _WIDE_SET)]
    g.region = basic[0] if basic else (regions.most_common(1)[0][0] if regions else "")
    facs = Counter(q.facility for q in g.quotes if q.facility)
    g.facility = facs.most_common(1)[0][0] if facs else ""
    g.incident_type = Counter(q.incident_type for q in g.quotes).most_common(1)[0][0]
    if g.org in _GENERIC_GOV and g.region:
        cand = canonical_org(g.region if g.region.endswith(_GENERIC_GOV[g.org]) else g.region + _GENERIC_GOV[g.org])
        g.org = cand if cand in ADMIN_NAMES or cand in _WIDE_ORGS else ""
    elif g.org in _GENERIC_GOV or g.org in _NOT_PUBLIC:
        g.org = ""
    if g.region == "세종" and g.org in _CENTRAL_KEYS:
        g.region = ""  # 정부세종청사 발 기사의 지명은 사건 지역이 아니다
    hz = Counter(h for q in g.quotes for h in q.hazards if h not in _GENERIC_HAZARD)
    g.keywords = [h for h, _ in hz.most_common(2)]
    if not g.keywords:
        leaf = g.rep.incident_detail.split(">")[-1] if g.rep.incident_detail.count(">") >= 2 else ""
        g.keywords = [k for k in [_DETAIL_KEYWORD.get(leaf, ""), *(h for q in g.quotes for h in q.hazards if h in ("참사", "사고"))] if k][:2]
    if not g.keywords:
        g.keywords = [w for w in _title_terms(g.rep.title, exclude=[g.region, g.org, g.facility])][:1]
    rep = g.rep
    s = 0.0
    if looks_occurred(rep.title) and not is_preventive(rep.title):
        s += 3
    if is_preventive(rep.title) and not (g.facility or any(q.dead for q in g.quotes)):
        s -= 4
    s += 2 if g.facility else 0
    s += 2 if basic else (1 if g.region else 0)
    s += 1 if any(q.strength == "확약" for q in g.quotes) else 0
    s += 1 if any(q.dead or q.injured for q in g.quotes) else 0
    s += 1 if g.org else -1
    s += min(len(g.news_ids), 6) * 0.3
    g.score = round(s, 2)


def _title_terms(title: str, exclude: list[str]) -> list[str]:
    out = []
    for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", title or ""):
        t = _PARTICLE_TAIL.sub("", t)
        if len(t) < 2 or t in _TITLE_STOP or t in out or any(e and (t in e or e in t) for e in exclude):
            continue
        if re.fullmatch(r"\d+[가-힣]*", t):
            continue
        out.append(t)
    return out


def attach_issues(groups: list[EventGroup], issues: list[IssueEvent]) -> list[IssueEvent]:
    """이슈 랭킹 사건을 같은 사건 그룹에 붙인다. 반환: 붙지 못한 이슈 사건 (인용문 보강 대상)."""
    unmatched = []
    for iss in issues:
        d = date.fromisoformat(iss.date)
        fam = hazard_family(iss.hazards)
        regs = {r.rstrip("시군구") if len(r) > 2 else r for r in iss.regions}
        hit = None
        for g in groups:
            if abs((g.occurred - d).days) > WINDOW_DAYS:
                continue
            if iss.facility and iss.facility == g.facility:
                hit = g
                break
            greg = {r.rstrip("시군구") if len(r) > 2 else r for q in g.quotes for r in q.regions}
            if regs & greg and fam & hazard_family([*g.keywords, *(h for q in g.quotes for h in q.hazards)]):
                hit = g
                break
        if hit is not None:
            if not hit.issue_topic:
                hit.issue_topic = iss.topic
                hit.issue_keywords = list(iss.keywords[:8])
                hit.score = round(hit.score + 2, 2)
        else:
            unmatched.append(iss)
    return unmatched


def select_groups(groups: list[EventGroup], limit: int, min_score: float = 2.0) -> list[EventGroup]:
    """(계열, 연도) 층을 돌아가며 점수순으로 limit 개. min_score 미만은 다른 층이 다 떨어졌을 때만."""
    strata: dict[tuple[str, int], list[EventGroup]] = defaultdict(list)
    for g in sorted(groups, key=lambda g: (-g.score, g.occurred)):
        strata[(g.incident_type, g.occurred.year)].append(g)
    picked: list[EventGroup] = []
    for floor in (min_score, float("-inf")):
        while len(picked) < limit:
            progressed = False
            for key in sorted(strata):
                lst = strata[key]
                while lst and lst[0] in picked:
                    lst.pop(0)
                if lst and lst[0].score >= floor and len(picked) < limit:
                    picked.append(lst.pop(0))
                    progressed = True
            if not progressed:
                break
        if len(picked) >= limit:
            break
    return sorted(picked, key=lambda g: g.occurred)


def seed_dict(g: EventGroup, idx: int) -> dict[str, Any]:
    rep = g.rep
    aliases = org_aliases(g.org)
    title = scrub(rep.title)[:120] or f"{g.region} {' '.join(g.keywords)}".strip()
    seed = {
        "key": f"x{rep.published.replace('-', '')}-{idx:03d}",
        "title": title,
        "incident_type": g.incident_type,
        "region": g.region,
        "facility": g.facility,
        "responsible_org": g.org,
        "org_aliases": aliases,
        "keywords": g.keywords,
        "occurred_at": rep.published,
        "summary": scrub(f"{rep.source}: {rep.quotation}")[:200],
        "source_news_id": rep.news_id,
        "source_quotation": scrub(rep.quotation)[:300],
        "auto_seed": True,
        "_score": g.score,
        "_source": scrub(rep.source),
        "_strength": rep.strength,
        "_action": scrub(rep.action),
        "_provider": rep.provider,
        "_link": rep.link,
        "_incident_detail": rep.incident_detail,
        "_via": sorted({q.via for q in g.quotes}),
        "_issue_topic": scrub(g.issue_topic),
        "_group_news_ids": g.news_ids[:30],
        "_group_size": len(g.news_ids),
        "_specific_terms": specific_terms(g),
        "_dead": max((q.dead for q in g.quotes if q.dead), default=None),
        "_injured": max((q.injured for q in g.quotes if q.injured), default=None),
    }
    seed["query"] = narrow_query(seed)
    return seed


_VERBISH_RE = re.compile(r"(된|한|는|은|을|를|운|던|며|고|게|서|지|요|까|나|죠|다|해|돼|LIVE|속보|종합|단독|영상|포토)$|^[A-Za-z]+$")


def specific_terms(g: EventGroup, limit: int = 4) -> list[str]:
    """이 사건만 가리키는 말: 그룹의 원천 기사 제목 2건 이상에 함께 나오는 명사꼴 + 이슈 랭킹 토픽 키워드.
    질의를 좁힐 때 이 말만 쓴다 (제목 한 건의 아무 단어로 좁히면 후속 기사가 다 빠진다)."""
    exclude = [g.region, g.org, g.facility, *g.keywords]
    cnt: Counter = Counter()
    for title in dict.fromkeys(q.title for q in g.quotes):
        for t in set(_title_terms(title, exclude)):
            cnt[t] += 1
    out = [t for t, n in cnt.most_common() if n >= 2 and len(t) >= 2 and not _VERBISH_RE.search(t)]
    for k in g.issue_keywords:
        k = _PARTICLE_TAIL.sub("", k)
        if len(k) >= 2 and k not in out and not _VERBISH_RE.search(k) and k not in _TITLE_STOP and not any(e and (k in e or e in k) for e in exclude) and k not in _GENERIC_HAZARD:
            out.append(k)
    return out[:limit]


def _q(t: str) -> str:
    return '"' + re.sub(r'["“”()]', " ", t).strip() + '"'


_WIDE_ORGS = frozenset({"서울시", "부산시", "대구시", "인천시", "광주시", "대전시", "울산시", "세종시", "경기도", "강원도", "충북", "충남", "전북", "전남", "경북", "경남", "제주도"})
_GENERIC_ORGS = frozenset({"경찰", "정부", "소방", "해경", "당국", "지자체", "소방당국", "군", "해군", "공군", "육군", "국방부"})
_GENERIC_FACILITY_RE = re.compile(r"^(\d+호선|[가-힣A-Za-z0-9]{1,2}(역|교|천|강|항|댐|선|보)|물류센터|데이터센터|지하주차장|[가-힣]{1,3}(아파트|공장|시장|센터))$")


def _wide(region: str) -> bool:
    return not region or region in _WIDE_SET or (region.endswith(("시", "도")) and region[:-1] in _WIDE_SET)


def narrow_query(seed: dict[str, Any]) -> str:
    """수집·인용문 검색에 함께 쓰는 좁은 질의 (실서버는 3년 치 후속 구간을 받으므로 넓은 질의는 사건당 호출이 수십 회로 늘어난다).

    - 고유 시설명이 있으면: "시설" [AND (지역 OR 지역 기관)]
    - 시설명이 일반적이면('9호선'·'서울역'·'물류센터'): "시설" AND (재난어) [AND (지역 OR 기관)]
    - 시설명이 없으면: (재난어) AND (기초 지역 OR 지역 기관). 광역·전국 단위면 제목 고유어 하나를 AND 로 더한다.
    중앙부처 별칭 OR('국토교통부' OR '국토부')은 넣지 않는다 — 모든 기사에 걸린다."""
    fac, region, org = seed["facility"], seed["region"], seed["responsible_org"]
    hz = [k for k in seed["keywords"] if k and k not in _GENERIC_HAZARD] or [k for k in seed["keywords"] if k]
    local_org = org and org not in _CENTRAL_KEYS and org not in _WIDE_ORGS and org not in _GENERIC_ORGS and len(org) >= 3 and not org.endswith(("청", "부", "공사", "공단", "본부"))
    scope = [t for t in dict.fromkeys([region, org if local_org or (org and not region and org not in _GENERIC_ORGS) else ""]) if t]
    scope_q = (" AND (" + " OR ".join(_q(t) for t in scope) + ")") if scope else ""
    hz_q = ("(" + " OR ".join(_q(t) for t in hz) + ")") if hz else ""
    if fac and not _GENERIC_FACILITY_RE.match(fac):
        return _q(fac) + scope_q
    if fac:
        return _q(fac) + (" AND " + hz_q if hz_q else "") + scope_q
    if not hz_q:
        hz_q = _q((_title_terms(seed["title"], [region, org]) or ["사고"])[0])
    if _wide(region) and seed.get("_specific_terms"):
        hz_q = hz_q + " AND " + _q(seed["_specific_terms"][0])  # 광역·전국 범위는 사건 고유어로 좁힌다 (없으면 건수 보정이 거른다)
    return hz_q + scope_q


def query_variants(seed: dict[str, Any]) -> list[str]:
    """넓은 것 → 좁은 것 순서의 후보 질의. calibrate_queries 가 time_line 건수로 고른다.
    좁힐 때는 시설·지역·재난어 조합과 사건 고유어(_specific_terms)만 쓴다."""
    base = narrow_query(seed)
    out = [base]
    fac, region = seed["facility"], seed["region"]
    hz = [k for k in seed["keywords"] if k and k not in _GENERIC_HAZARD]
    if fac and region:
        out.append(_q(fac) + " AND " + _q(region))
    if len(hz) >= 2:
        out.append(" AND ".join(_q(k) for k in hz[:2]) + (" AND " + _q(region) if region else ""))
    for t in seed.get("_specific_terms") or []:
        out.append(base + " AND " + _q(t))
    return list(dict.fromkeys(out))


def calibrate_queries(client, seeds: list[dict[str, Any]], today: date, limit: int, stats: dict[str, Any]) -> None:
    """사건별로 (발생일-1 ~ 기준일) time_line 총건수가 limit 이하가 될 때까지 질의를 좁힌다 (질의당 1회 호출).
    collect 는 건수가 상한 안이면 사건당 4회로 끝나지만, 넘치면 월·순별 상태 질의로 사건당 100회 넘게 부른다."""
    from datetime import timedelta

    from ..bigkinds.schemas import PublishedAt, TimeLineArgument

    stats.setdefault("calibrated", 0)
    stats.setdefault("over_limit", 0)
    for s in seeds:
        start = date.fromisoformat(s["occurred_at"]) - timedelta(days=1)
        chosen, hits = None, None
        for q in query_variants(s):
            try:
                tl = client.time_line(TimeLineArgument(query=q, published_at=PublishedAt.of(start, today + timedelta(days=1)), interval="month"))
                n = sum(int(p.hits or 0) for p in tl.time_line) or int(tl.total_hits or 0)
            except Exception:  # noqa: BLE001 — 건수를 못 보면 그 후보는 건너뛴다
                continue
            chosen, hits = q, n
            if n <= limit:
                break
        if chosen is not None:
            if chosen != s["query"]:
                stats["calibrated"] += 1
            s["query"], s["_query_hits"] = chosen, hits
            if hits is not None and hits > limit:
                stats["over_limit"] += 1


def write_draft(groups: list[EventGroup], path: Path, header: dict[str, Any], seeds: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    seeds = seeds if seeds is not None else [seed_dict(g, i) for i, g in enumerate(groups, 1)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(
            "# 실험 자동 시드 (v2 §4.2) — scripts/experiment.py --stage seed 가 만든다. 사람이 훑어 고치면 seeds/events.yaml 로 옮기고\n"
            "# `--seeds` 로 다시 돌린다. '_' 로 시작하는 키는 참고용(로더가 읽지 않음). 기사 문장은 API 반환 범위(인용문 300자·제목)만 담는다.\n"
        )
        yaml.safe_dump({"meta": header, "events": seeds}, f, allow_unicode=True, sort_keys=False, width=200)
    return seeds


def save_groups(groups: list[EventGroup], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"score": g.score, "occurred": g.rep.published, "org": g.org, "region": g.region, "facility": g.facility, "incident_type": g.incident_type,
         "keywords": g.keywords, "issue_topic": g.issue_topic, "title": g.rep.title, "news_ids": g.news_ids, "quotes": [asdict(q) for q in g.quotes]}
        for g in sorted(groups, key=lambda g: -g.score)
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def build_groups(res: DiscoverResult) -> tuple[list[EventGroup], list[IssueEvent]]:
    for q in res.quotes:
        q.org = canonical_org(normalize_actor_org(q.source) or "") or ""  # 기관 사전이 바뀌어도 다시 계산
        if _NOT_PUBLIC_RE.search(q.org):
            q.org = ""
        q.facility = find_facility(q.title)  # 시설명은 제목에서만 (규칙이 바뀌어도 discover 를 다시 부르지 않게 여기서 다시 계산)
    groups = group_quotes(res.quotes)
    unmatched = attach_issues(groups, res.issues)
    return groups, unmatched
