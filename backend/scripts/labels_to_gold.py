"""사람 라벨 CSV(gold/experiment_labels.csv + cluster_sample.csv) → 정답셋 YAML (scripts/evaluate.py 형식, v2 §4.5).

  .venv/bin/python scripts/labels_to_gold.py                       # → gold/events.yaml (실험 라벨만 있을 때)
  .venv/bin/python scripts/labels_to_gold.py --out gold/experiment_gold.yaml

라벨된 사건만 옮긴다 (사람_후속보도 또는 군집 표본 중 하나라도 O/X 가 있는 사건).
  - linked_news_ids   : 군집 표본에서 사람이 O 로 표시한 기사 + 원천 기사 (부분 라벨 — 표본 밖 기사는 모른다)
  - followup_news_ids : 사람_후속보도 = O 이고 사람_근거_news_id 가 있으면 그 기사 (쉼표·공백으로 여러 개)
  - expected_status   : 이번 라벨에는 상태 정답이 없어 넣지 않는다 (evaluate.py 는 상태 지표 분모에서 뺀다)
기존 출력 파일에 실험 밖 사건(키가 x 로 시작하지 않음)이 있으면 덮어쓰지 않는다 (--force 로 무시).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_DIR  # noqa: E402


def _ox(v: str | None) -> bool | None:
    s = (v or "").strip().upper()
    return True if s in ("O", "Y", "YES", "예") else False if s in ("X", "N", "NO", "아니오") else None


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build(gold_dir: Path) -> dict:
    exp = _read(gold_dir / "experiment_labels.csv")
    cl = _read(gold_dir / "cluster_sample.csv")
    cl_by: dict[str, list[dict[str, str]]] = {}
    for r in cl:
        cl_by.setdefault(r.get("사건키", ""), []).append(r)
    events = []
    for r in exp:
        key = r.get("사건키", "")
        f = _ox(r.get("사람_후속보도(O/X)"))
        sample = [c for c in cl_by.get(key, []) if _ox(c.get("사람_OK(O/X)")) is not None]
        if f is None and not sample:
            continue
        linked = [c["news_id"] for c in sample if _ox(c.get("사람_OK(O/X)"))]
        if r.get("원천기사_news_id"):
            linked = [r["원천기사_news_id"], *[n for n in linked if n != r["원천기사_news_id"]]]
        ev: dict = {"key": key, "title": r.get("사건명", ""), "linked_news_ids": [str(n) for n in linked]}
        if f is not None:
            ids = [n for n in re.split(r"[,\s]+", r.get("사람_근거_news_id", "") or "") if n]
            ev["followup_news_ids"] = [str(n) for n in ids] if f else []
            ev["human_followup"] = bool(f)
        if r.get("사람_메모"):
            ev["note"] = r["사람_메모"]
        events.append(ev)
    return {"generated_for": "experiment-labels", "as_of": date.today().isoformat(), "events": events}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-dir", default=str(BACKEND_DIR / "gold"))
    ap.add_argument("--out", default=str(BACKEND_DIR / "gold" / "events.yaml"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists() and not args.force:
        old = yaml.safe_load(out.read_text(encoding="utf-8")) or {}
        foreign = [e.get("key") for e in old.get("events", []) or [] if not str(e.get("key", "")).startswith("x")]
        if foreign:
            print(f"{out} 에 실험 밖 사건 {len(foreign)}건이 있어 덮어쓰지 않는다 (--out 다른 파일 또는 --force)")
            return 2
    data = build(Path(args.gold_dir))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write("# scripts/labels_to_gold.py 가 사람 라벨 CSV 에서 만든 정답셋. news_id 는 문자열.\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    print(f"{out}: 라벨된 사건 {len(data['events'])}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
