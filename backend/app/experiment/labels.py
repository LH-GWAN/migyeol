"""§4.5 사람 라벨 CSV 3종 — 시스템 열은 채우고 사람 열만 비운다. 다시 내보내도 이미 채운 사람 열은 보존한다.

  gold/experiment_labels.csv : 사건별 후속보도 판정 교차 검증
  gold/cluster_sample.csv    : 사건당 자동 연결 기사 무작위 5건 (군집 정밀도)
  gold/org_sample.csv        : 발언 주체 원문 → 정규화 기관 쌍 (기관 정규화 정확도)

사람 열 값은 O / X (대소문자·공백 무시). 라벨이 일부만 있어도 지표를 낸다(분모 = 라벨된 건수).
"""

from __future__ import annotations

import csv
import random
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Article, EventArticle, Quotation
from ..rules import actor_search
from ..rules.actors import normalize_actor_org
from ..rules.orgs import canonical_org
from .metrics import EventFacts
from .text import scrub

EXPERIMENT_COLS = [
    "사건키", "사건명", "발생일", "재난유형", "책임기관", "원천기사_news_id", "원천기사_제목", "원천기사_링크", "원천_발언",
    "시스템_후속보도", "시스템_상태", "첫후속_news_id", "첫후속_제목", "첫후속_링크", "첫후속_날짜", "첫후속_신호", "근거_발췌", "연결기사수", "후속기사수",
    "사람_후속보도(O/X)", "사람_근거_news_id", "사람_군집OK(O/X)", "사람_기관명OK(O/X)", "사람_메모",
]
EXPERIMENT_HUMAN = ["사람_후속보도(O/X)", "사람_근거_news_id", "사람_군집OK(O/X)", "사람_기관명OK(O/X)", "사람_메모"]
CLUSTER_COLS = ["사건키", "사건명", "news_id", "제목", "발행일", "언론사", "링크", "자동판단", "연결점수", "연결사유", "사람_OK(O/X)", "사람_메모"]
CLUSTER_HUMAN = ["사람_OK(O/X)", "사람_메모"]
ORG_COLS = ["원문", "정규화", "출처", "건수", "예시_사건키", "사람_OK(O/X)", "사람_정답"]
ORG_HUMAN = ["사람_OK(O/X)", "사람_정답"]
CLUSTER_PER_EVENT = 5
ORG_SAMPLE_MAX = 300


def _read(path: Path, key_cols: list[str]) -> dict[tuple, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {tuple(r.get(k, "") for k in key_cols): r for r in csv.DictReader(f)}


def _write(path: Path, cols: list[str], rows: list[dict[str, Any]], human: list[str], key_cols: list[str], regrade=None) -> int:
    old = _read(path, key_cols)
    seen: set[tuple] = set()
    for r in rows:
        key = tuple(str(r.get(k, "")) for k in key_cols)
        seen.add(key)
        prev = old.get(key)
        for h in human:
            r[h] = (prev or {}).get(h, "") if prev else r.get(h, "")
        if prev and regrade is not None:
            r.update(regrade(r, prev))
    # 사람 라벨이 붙었는데 이번 집계에 없는 행은 지우지 않고 뒤에 남긴다 (라벨 유실 방지 — 10-30 D2·exp-3)
    for key, prev in old.items():
        if key not in seen and any((prev.get(h) or "").strip() for h in human):
            rows.append(dict(prev))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글이 깨지지 않게 BOM
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: scrub(str(r.get(c, "") if r.get(c) is not None else "")) for c in cols})
    return len(rows)


def write_experiment_labels(session: Session, facts: list[EventFacts], path: Path) -> int:
    rows = []
    for f in facts:
        ev = f.event
        src = session.get(Article, ev.source_news_id) if ev.source_news_id else None
        first = f.first_valid
        first_any = f.followups[0] if f.followups else None
        a = first[1] if first else None
        rows.append({
            "사건키": ev.key, "사건명": ev.title, "발생일": ev.occurred_at.isoformat(), "재난유형": ev.incident_type, "책임기관": f.org,
            "원천기사_news_id": ev.source_news_id or "", "원천기사_제목": src.title if src else "", "원천기사_링크": (src.provider_link_page if src else "") or "",
            "원천_발언": ev.source_quotation or "",
            "시스템_후속보도": "있음" if f.has_followup else "없음", "시스템_상태": ev.status,
            "첫후속_news_id": (a.news_id if a else (first_any.news_id if first_any else "")),
            "첫후속_제목": (a.title if a else (first_any.title if first_any else "")),
            "첫후속_링크": ((a.provider_link_page if a else (first_any.provider_link_page if first_any else "")) or ""),
            "첫후속_날짜": ((a.published_at.isoformat() if a and a.published_at else (first_any.published_at.isoformat() if first_any and first_any.published_at else ""))),
            "첫후속_신호": first[0].signal if first else "", "근거_발췌": first[0].evidence_span if first else "",
            "연결기사수": len(f.linked), "후속기사수": len(f.followups),
        })
    return _write(path, EXPERIMENT_COLS, rows, EXPERIMENT_HUMAN, ["사건키"])


CLUSTER_UNLINKED = "연결해제"  # 라벨된 표본 행인데 지금은 자동 연결이 아님 (게이트·임계값 변경으로 빠짐) — 정밀도 분모에서 뺀다


def write_cluster_sample(session: Session, facts: list[EventFacts], path: Path, per_event: int = CLUSTER_PER_EVENT) -> int:
    """사건당 자동 연결 표본. 사람 라벨이 붙은 사건은 그 표본 행을 그대로 유지한다 — 연결 규칙을 바꾼 뒤 다시 뽑으면 라벨이 사라지므로.
    유지된 행 중 지금 자동 연결이 아닌 것은 자동판단='연결해제' 로 적는다 (label_metrics 의 군집 정밀도 = 지금도 자동 연결인 라벨 행)."""
    old = _read(path, ["사건키", "news_id"])
    labeled: dict[str, list[dict[str, str]]] = {}
    for (k, _), r in old.items():
        if any((r.get(h) or "").strip() for h in CLUSTER_HUMAN):
            labeled.setdefault(k, []).append(r)
    rows = []
    for f in facts:
        ev = f.event
        all_links = {l.news_id: l for l in session.query(EventArticle).filter_by(event_id=ev.id).all()}
        if ev.key in labeled:
            for r in sorted(labeled[ev.key], key=lambda r: r["news_id"]):
                l = all_links.get(r["news_id"])
                auto = l is not None and l.is_active and not l.confirmed and l.link_method in ("rule", "embedding")
                rows.append({
                    **{c: r.get(c, "") for c in CLUSTER_COLS if c not in CLUSTER_HUMAN},
                    "사건명": ev.title, "자동판단": l.link_method if auto else CLUSTER_UNLINKED,
                    "연결점수": round(l.link_score or 0.0, 3) if l is not None else "", "연결사유": l.link_reason if l is not None else "",
                })
            continue
        links = [l for l in all_links.values() if l.is_active and l.link_method in ("rule", "embedding")]
        rng = random.Random(ev.key)  # 사건 키로 고정 — 다시 내보내도 같은 표본
        sample = sorted(rng.sample(sorted(links, key=lambda l: l.news_id), min(per_event, len(links))), key=lambda l: l.news_id)
        for l in sample:
            a = l.article
            rows.append({
                "사건키": ev.key, "사건명": ev.title, "news_id": l.news_id, "제목": a.title if a else "", "발행일": a.published_at.isoformat() if a and a.published_at else "",
                "언론사": a.provider if a else "", "링크": (a.provider_link_page if a else "") or "", "자동판단": l.link_method, "연결점수": round(l.link_score or 0.0, 3),
                "연결사유": l.link_reason,
            })
    return _write(path, CLUSTER_COLS, rows, CLUSTER_HUMAN, ["사건키", "news_id"])


def org_pairs(session: Session, facts: list[EventFacts]) -> list[dict[str, Any]]:
    counter: Counter = Counter()
    example: dict[tuple[str, str, str], str] = {}
    for f in facts:
        ev = f.event
        if ev.responsible_org:
            k = (ev.responsible_org, canonical_org(ev.responsible_org), "시드 책임기관")
            counter[k] += 1
            example.setdefault(k, ev.key)
        for q in session.query(Quotation).filter_by(event_id=ev.id, is_public_actor=True).all():
            raw = (q.source or "").strip()
            if not raw:
                continue
            org = normalize_actor_org(raw, ev.responsible_org, ev.org_aliases or [])
            lexicon = [ev.region, ev.responsible_org, *(ev.org_aliases or [])]
            if actor_search.wants(raw, org, lexicon):  # 이름+직함('김동연 지사')은 뉴스 검색 캐시로 (rules/actor_search.py)
                org = actor_search.lookup(raw, q.published_at, ev.region, lexicon) or org
            canon = canonical_org(org or raw)
            k = (raw, canon, "인용문 발언 주체")
            counter[k] += 1
            example.setdefault(k, ev.key)
    rows = [{"원문": raw, "정규화": canon, "출처": src, "건수": n, "예시_사건키": example[(raw, canon, src)]} for (raw, canon, src), n in counter.most_common(ORG_SAMPLE_MAX)]
    return rows


def write_org_sample(session: Session, facts: list[EventFacts], path: Path) -> list[dict[str, Any]]:
    rows = org_pairs(session, facts)
    # 사람 라벨이 붙은 쌍은 이번 상위 300 에서 빠져도 남긴다(건수 0) — 연결·인용문이 바뀌어 다시 내보내도 라벨이 사라지지 않게
    keys = {(r["원문"], r["출처"]) for r in rows}
    kept = [
        {**{c: r.get(c, "") for c in ORG_COLS}, "건수": 0}
        for k, r in _read(path, ["원문", "출처"]).items()
        if k not in keys and any((r.get(h) or "").strip() for h in ORG_HUMAN)
    ]
    # 같은 (원문, 출처) 가 정규화만 달리 두 줄일 수 있다('경찰 관계자' → 경찰 / 강원경찰청) — 정규화까지 같은 옛 줄이 있으면 그 라벨을 그대로 쓴다
    exact = _read(path, ["원문", "출처", "정규화"])

    def regrade(row: dict[str, Any], prev: dict[str, str]) -> dict[str, str]:
        same = exact.get((str(row.get("원문", "")), str(row.get("출처", "")), str(row.get("정규화", ""))))
        return {h: same.get(h, "") for h in ORG_HUMAN} if same else _regrade_org(row, prev)

    _write(path, ORG_COLS, rows + kept, ORG_HUMAN, ["원문", "출처"], regrade=regrade)
    return rows


def _regrade_org(row: dict[str, Any], prev: dict[str, str]) -> dict[str, str]:
    """정규화 결과가 라벨을 붙일 때와 달라진 행은 라벨의 정답과 기계 대조로 O/X 를 다시 매긴다 (§15).
    정답 = 사람_정답, 비어 있고 옛 라벨이 O 면 옛 정규화. 같은 기관인지는 canonical_org 로 본다 ('강원' = '강원도').
    옛 라벨이 '원문 그대로 둠'(정규화 = 원문)을 O 로 본 것이면 새 기관의 맞고 틀림을 알 수 없어 라벨을 비운다(다시 붙일 대상)."""
    ok = _ox(prev.get("사람_OK(O/X)"))
    old_norm, new_norm = (prev.get("정규화") or "").strip(), str(row.get("정규화") or "").strip()
    if ok is None or old_norm == new_norm:
        return {}
    answer = (prev.get("사람_정답") or "").strip()
    if not answer and ok and old_norm == str(row.get("원문") or "").strip():
        return {"사람_OK(O/X)": "", "사람_정답": ""}  # '원문 그대로 둠' 을 O 로 본 라벨 — 새 기관은 다시 봐야 한다 (분모에서 빠짐)
    answer = answer or (old_norm if ok else "")
    if not answer:
        return {}
    same = canonical_org(answer) == canonical_org(new_norm)
    return {"사람_OK(O/X)": "O" if same else "X", "사람_정답": answer}


def _ox(v: str | None) -> bool | None:
    s = (v or "").strip().upper()
    return True if s in ("O", "Y", "YES", "예") else False if s in ("X", "N", "NO", "아니오") else None


def label_metrics(gold_dir: Path) -> dict[str, Any]:
    """CSV 의 사람 열로 precision/recall 등. 라벨이 없으면 None (분모 = 라벨된 건수)."""
    exp = list(_read(gold_dir / "experiment_labels.csv", ["사건키"]).values())
    labeled = [(r, _ox(r.get("사람_후속보도(O/X)"))) for r in exp]
    labeled = [(r, v) for r, v in labeled if v is not None]
    sys_pos = [(r, v) for r, v in labeled if r.get("시스템_후속보도") == "있음"]
    human_pos = [(r, v) for r, v in labeled if v]
    precision = round(sum(1 for _, v in sys_pos if v) / len(sys_pos), 3) if sys_pos else None
    recall = round(sum(1 for r, _ in human_pos if r.get("시스템_후속보도") == "있음") / len(human_pos), 3) if human_pos else None
    cl = [_ox(r.get("사람_OK(O/X)")) for r in _read(gold_dir / "cluster_sample.csv", ["사건키", "news_id"]).values() if r.get("자동판단") != CLUSTER_UNLINKED]
    cl = [v for v in cl if v is not None]
    og = [_ox(r.get("사람_OK(O/X)")) for r in _read(gold_dir / "org_sample.csv", ["원문", "출처"]).values()]
    og = [v for v in og if v is not None]
    return {
        "labeled": len(labeled),
        "followup_precision": precision,
        "followup_recall": recall,
        "cluster_precision": round(sum(cl) / len(cl), 3) if cl else None,
        "org_accuracy": round(sum(og) / len(og), 3) if og else None,
        "_cluster_labeled": len(cl),
        "_org_labeled": len(og),
    }
