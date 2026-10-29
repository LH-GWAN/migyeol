"""'이름 + 직함' 만 있는 발언 주체를 뉴스 검색으로 기관에 잇는다 ('김동연 지사' → 경기도, '이상민 장관' → 행정안전부).

인용문 source 는 '김동연 지사'·'원희룡 장관'·'정철원 군수' 처럼 기관명이 빠진 형태가 흔하다. 사전으로는 사람과 기관을 잇지
못하므로(재임이 바뀐다), 기사 날짜 전후 창에서 빅카인즈 뉴스 검색 `"이름" AND "직함"` 을 하고 제목·하이라이트·본문 앞 200자의
'이름 + 기관 + 직함' 표기('김동연 경기도지사', '원희룡 국토교통부 장관', '김찬진 인천 동구청장')를 문서 단위로 센다.

- 기간 버킷: 발언일의 분기(2025Q1) + 앞뒤 45일 (≈ 발언일 ±90일). 재임 변동 때문에 창은 필수다.
- 판정: 1위 기관 근거 문서 ≥ MIN_DOCS 이고 1위가 2위의 MIN_RATIO 배 이상일 때만. 아니면 해석하지 않는다(원문 유지).
  사건 지역(event.region)과 맞는 지자체 후보가 MIN_DOCS 이상이면 그것을 우선한다 (동명이인·다른 지역 단체장).
- 캐시: (이름, 직함, 버킷) → {질의, 창, 전체 건수, 근거 문서 수, 후보 분포} 를 fixtures/actor_orgs.json 에 남긴다(커밋).
  정규화(store_promise·org_pairs)는 캐시만 읽는다 — 네트워크 없음. 검색은 ensure_cache() 가 하고, record 모드 녹화로 재생된다.
- 개인명은 캐시 키에만 남는다. 추적 키·화면에는 기관만 쓰고 원문(actor_raw)은 그대로 둔다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..config import BACKEND_DIR
from .actors import person_title
from .orgs import canonical_org, is_known_org
from .regions import WIDE, region_base, region_parents

log = logging.getLogger("migyeol.actor_search")

CACHE_PATH = BACKEND_DIR / "fixtures" / "actor_orgs.json"
CACHE_VERSION = 1
PAD_DAYS = 45  # 분기 앞뒤 여유 — 분기(≈90일) + 양쪽 45일 ≈ 발언일 ±90일
RETURN_SIZE = 30
FIELDS = ["news_id", "title", "hilight", "content", "published_at"]
SORT = {"_score": "desc"}
MIN_DOCS = 3
MIN_RATIO = 2.0

# 직함 → 검색·표기 대조에 쓰는 핵심형 ('도지사' 는 '전북지사' 로도 쓰여 '지사' 로 찾는다)
_CORE_TITLE = {
    "도지사": "지사", "행정부지사": "부지사", "행정1부지사": "부지사", "행정2부지사": "부지사", "경제부지사": "부지사",
    "행정부시장": "부시장", "경제부시장": "부시장", "국무총리": "총리", "제1차관": "차관", "제2차관": "차관",
}
_JOSA_START = re.compile(r"^(?:은|는|이|가|의|와|과|도|를|을|에|께서|측)(?:\s|$)")
_LOCAL_TITLES = frozenset({"지사", "부지사", "시장", "부시장", "군수", "부군수", "구청장", "부구청장"})
_BARE_WIDE = frozenset(WIDE)
_TAG_RE = re.compile(r"</?b>|<[^>]{0,20}>")
# 이름 뒤 기관 표기: 공백·가운뎃점·한글·숫자·영문만 (쉼표·괄호·따옴표가 끼면 다른 사람·다른 구절로 본다)
_PHRASE_CHARS = r"[가-힣0-9A-Za-z·\s]"
_MAX_PHRASE = 20
# 이름 뒤 표기에 이런 토큰이 끼면 민간 조직 직함이다 ('곽성일 NH농협은행 인천본부장' 은 인천시가 아니다)
_PRIVATE_TAILS = (
    "은행", "금융", "농협", "보험", "증권", "그룹", "지주", "회사", "건설", "산업", "노조", "노총", "협회", "조합", "재단", "연구원", "연구소",
    "당", "일보", "신문", "방송", "대학교", "대학", "병원", "센터장", "기업", "캐피탈", "카드", "생명", "화재", "전자", "자동차",
)


def core_title(title: str) -> str:
    return _CORE_TITLE.get(title, title)


def bucket_of(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def bucket_window(bucket: str) -> tuple[date, date]:
    """'2025Q1' → (2024-11-17, 2025-05-16) — 끝은 빅카인즈 until(제외)."""
    y, q = int(bucket[:4]), int(bucket[-1])
    start = date(y, 3 * (q - 1) + 1, 1)
    end = date(y + 1, 1, 1) if q == 4 else date(y, 3 * q + 1, 1)
    return start - timedelta(days=PAD_DAYS), end + timedelta(days=PAD_DAYS)


def search_query(name: str, title: str) -> str:
    return f'"{name}" AND "{core_title(title)}"'


def cache_key(name: str, title: str, bucket: str) -> str:
    return f"{name}|{core_title(title)}|{bucket}"


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text or "")).strip()


def mentions(text: str | None, name: str, title: str) -> list[str]:
    """본문에서 '이름 + 기관 + 직함' 표기를 찾아 정규 기관 키 목록으로 ('김동연 경기도지사가' → ['경기도']).
    '김동연 지사'(기관 없음)·'김동연, 김관영 전북지사'(쉼표)·'김동연은 경기도지사'(조사)는 세지 않는다."""
    t = _clean(text)
    core = core_title(title)
    pat = re.compile(r"(" + _PHRASE_CHARS + r"{0," + str(_MAX_PHRASE) + r"}?)" + re.escape(core))
    out: list[str] = []
    for m in re.finditer(re.escape(name), t):
        if m.start() > 0 and re.match(r"[가-힣]", t[m.start() - 1]):
            continue  # 더 긴 낱말의 일부
        after = t[m.end(): m.end() + _MAX_PHRASE + len(core) + 1]
        mm = pat.match(after)
        if not mm:
            continue
        head = mm.group(1).strip()
        if not head or _JOSA_START.match(head):
            continue
        if core not in _LOCAL_TITLES and head in _BARE_WIDE:
            continue  # '곽성일 인천본부장' — 광역명 + 본부장은 어느 기관 지역본부인지 모른다 (지사·시장·군수만 광역명으로 기관이 정해진다)
        if any(tok.endswith(_PRIVATE_TAILS) for tok in head.split()[:-1] or head.split()) and not head.endswith(("시", "군", "구", "도")):
            continue
        org = canonical_org(after[: mm.end()].strip())
        if org and is_known_org(org):
            out.append(org)
    return out


def tally(documents: Iterable[Any], name: str, title: str) -> tuple[Counter, int]:
    """문서 단위로 센다 (한 문서가 같은 기관을 여러 번 써도 1). 반환: (기관 → 근거 문서 수, 표기가 하나라도 있는 문서 수)."""
    dist: Counter = Counter()
    docs = 0
    for d in documents:
        get = d.get if isinstance(d, dict) else (lambda k, _d=d: getattr(_d, k, None))
        found = set()
        for field in ("title", "hilight", "content"):
            found.update(mentions(get(field), name, title))
        if found:
            docs += 1
            dist.update(found)
    return dist, docs


def region_match(org: str, region: str | None) -> bool:
    """후보 기관이 사건 지역의 지자체인가 ('경기도' ~ '성남시', '동구' ~ '인천 동구', '담양군' ~ '전남')."""
    from .orgs import _WIDE_CANON
    from .regions import is_wide_region

    if not region or not org:
        return False
    if region_base(org) == region_base(region):
        return True
    po, pr = region_parents(org), region_parents(region)
    if not po or not pr:
        return False
    if org in set(_WIDE_CANON.values()):
        return po <= pr  # 광역 후보: 사건 지역이 그 광역 안
    return is_wide_region(region) and bool(po & pr)  # 기초 후보: 사건 지역이 광역일 때만 (기초끼리는 이름이 같아야)


@dataclass
class Decision:
    org: str | None
    docs: int
    reason: str


def decide(dist: dict[str, int], region: str | None = None, min_docs: int = MIN_DOCS, min_ratio: float = MIN_RATIO) -> Decision:
    ranked = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))
    if not ranked:
        return Decision(None, 0, "표기 없음")
    local = [(o, n) for o, n in ranked if region_match(o, region) and n >= min_docs]
    if local:
        o, n = local[0]
        rest = max((m for x, m in ranked if x != o and not region_match(x, region)), default=0)
        if (o, n) == ranked[0] or rest < n * min_ratio:  # 지역 후보가 다른 지역 1위에 크게 밀리면(2배 이상) 보류
            return Decision(o, n, "사건 지역 일치")
    top, n1 = ranked[0]
    n2 = ranked[1][1] if len(ranked) > 1 else 0
    if n1 < min_docs:
        return Decision(None, n1, f"근거 부족 ({n1} < {min_docs})")
    if n2 and n1 < n2 * min_ratio:
        return Decision(None, n1, f"후보 경합 ({ranked[0][0]} {n1} · {ranked[1][0]} {n2}) — 동명이인·재임 변동 의심")
    return Decision(top, n1, "최다 표기")


# ---------------------------------------------------------------- 캐시
_cache: dict[str, Any] | None = None
_cache_path: Path | None = None


def cache_path() -> Path:
    p = os.environ.get("ACTOR_CACHE_PATH")
    return Path(p) if p else CACHE_PATH


def load_cache(path: Path | None = None) -> dict[str, Any]:
    global _cache, _cache_path
    path = path or cache_path()
    if _cache is not None and _cache_path == path:
        return _cache
    data: dict[str, Any] = {"version": CACHE_VERSION, "entries": {}}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("이름+직함 캐시를 읽지 못함 %s (%s) — 빈 캐시로", path.name, exc)
    data.setdefault("entries", {})
    _cache, _cache_path = data, path
    return data


def save_cache(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": CACHE_VERSION, "entries": dict(sorted(data.get("entries", {}).items()))}
    fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    reset_cache()


def reset_cache() -> None:
    global _cache, _cache_path
    _cache, _cache_path = None, None


def wants(source: str, rule_org: str | None, lexicon: Iterable[str] = ()) -> bool:
    """검색 대상인가: 이름+직함 형태이고, 기존 규칙(normalize_actor_org) 결과가 없거나 아는 기관이 아니다
    ('안병윤 부시장' → 규칙 결과 '부시' 는 기관이 아니다. '김민석 총리' → '총리' = 국무총리 는 이미 기관)."""
    if not person_title(source, [x for x in lexicon if x]):
        return False
    return not rule_org or not is_known_org(canonical_org(rule_org))


def lookup(source: str, published: date | None, region: str | None = None, lexicon: Iterable[str] = ()) -> str | None:
    """캐시만 본다 (네트워크 없음). 이름+직함 형태가 아니거나, 캐시에 없거나, 판정 보류면 None."""
    pt = person_title(source, [x for x in lexicon if x])
    if not pt or published is None:
        return None
    e = load_cache()["entries"].get(cache_key(*pt, bucket_of(published)))
    if not e:
        return None
    return decide(e.get("dist") or {}, region).org


def needed(items: Iterable[tuple[str, date | None, Iterable[str]]]) -> list[tuple[str, str, str]]:
    """(source, 발언일, 사전) 들에서 캐시에 없는 (이름, 핵심 직함, 버킷) 을 빈도순으로."""
    entries = load_cache()["entries"]
    cnt: Counter = Counter()
    for source, published, lexicon in items:
        pt = person_title(source, [x for x in lexicon if x])
        if not pt or published is None:
            continue
        k = (pt[0], core_title(pt[1]), bucket_of(published))
        if cache_key(*k) not in entries:
            cnt[k] += 1
    return [k for k, _ in cnt.most_common()]


def search_one(client: Any, name: str, title: str, bucket: str) -> dict[str, Any]:
    from ..bigkinds.schemas import NewsSearchArgument, PublishedAt

    start, until = bucket_window(bucket)
    q = search_query(name, title)
    res = client.search_news(NewsSearchArgument(query=q, published_at=PublishedAt.of(start, until), sort=dict(SORT), return_size=RETURN_SIZE, fields=list(FIELDS)))
    dist, docs = tally(res.documents, name, title)
    return {
        "name": name, "title": core_title(title), "bucket": bucket, "query": q, "window": [start.isoformat(), until.isoformat()],
        "total_hits": res.total_hits, "returned": len(res.documents), "docs_with_org": docs, "dist": dict(dist.most_common()),
    }


def ensure_cache(client: Any, items: Iterable[tuple[str, date | None, Iterable[str]]], limit: int | None = None) -> dict[str, int]:
    """캐시에 없는 (이름, 직함, 버킷) 만 검색한다. 녹화에 없는데 replay_only 면 BigKindsError → 건너뛴다(다음 실행에서 다시)."""
    from ..bigkinds.protocol import BigKindsError
    from ..redact import mask

    todo = needed(items)
    if limit is not None:
        todo = todo[:limit]
    stats = {"needed": len(todo), "searched": 0, "failed": 0}
    if not todo:
        return stats
    data = load_cache()
    for name, title, bucket in todo:
        try:
            data["entries"][cache_key(name, title, bucket)] = search_one(client, name, title, bucket)
            stats["searched"] += 1
        except BigKindsError as exc:
            stats["failed"] += 1
            log.warning("이름+직함 검색 실패 %s/%s/%s: %s", name, title, bucket, mask(str(exc))[:120])
    save_cache(data)
    return stats
