"""3단계 — (원천 발언, 후보 기사) 교차 인코더, 사건 단위 중첩 교차검증 (docs/연구.md 3.8).

  cd backend
  .venv/bin/python scripts/train_followup_cross.py                     # followup_pairs.pkl → data/experiment/followup_cross_oof.csv
  .venv/bin/python scripts/train_followup_cross.py --variant action    # 원천 발언 대신 원천 발언의 다짐 절(없으면 발언 전체)

입력 쌍 = (원천 발언: [책임기관] 발언 앞 200자, 기사: [원천 발언 뒤 경과] 제목 하이라이트). 정답은 train_followup_pairs.article_label(--labels) —
1 = 라벨 '있음' 사건의 라벨 근거 기사와 그 전재, 0 = 라벨 '없음' 사건의 후보와 '있음' 사건의 그 밖의 후보(unknown0, 기본).
'있음' 사건의 나머지를 빼면(base) 사건마다 정답이 하나로 갈려, 모델이 기사는 보지 않고 원천 발언만으로 외운다(평가 fold 기사 AUC 0.50 — 3.8 ④).
모두 Claude 에이전트 라벨에서 끌어낸 학습용 정답이고, 평가는 사건 라벨 90건이다.

중첩: 바깥 fold k 마다 (1) 학습 사건 전체로 학습 → 평가 fold 점수(outer), (2) 학습 사건 안에서 안쪽 fold j 를 빼고 학습 → j 점수(inner).
inner 점수는 문턱 고르기와 2단 쌓기(피처 모델에 점수를 넣기)에 쓴다 — 평가 fold 의 라벨은 어디에도 쓰지 않는다.
모델 = jhgan/ko-sroberta-multitask + 회귀 머리 1개, BCE. 양성이 적어(사건당 1~3건) epoch 마다 음성을 표본으로 뽑고 양성을 반복한다.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import train_followup_pairs as TP  # noqa: E402

DATA = BACKEND / "data" / "experiment"
MODEL = "jhgan/ko-sroberta-multitask"


def days_tag(ev: dict, c: dict) -> str:
    d = (c["published"] - (ev["src_date"] or ev["occurred"])).days
    return "[다음날]" if d <= 1 else "[1주내]" if d <= 7 else "[1달내]" if d <= 31 else "[1년내]" if d <= 365 else "[1년뒤]"


def texts(ev: dict, c: dict, variant: str) -> tuple[str, str]:
    org = ev["resp"][0] if ev["resp"] else ""
    src = ev["src_action"] if variant == "action" and ev["src_action"] else ev["src_quote"]
    return f"[{org}] {src[:200]}", f"{days_tag(ev, c)} {c['title']} {c['hilight']}".strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="quote", choices=("quote", "action"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--neg-per-epoch", type=int, default=2500, help="epoch 마다 뽑는 음성 수")
    ap.add_argument("--pos-repeat", type=int, default=4, help="epoch 마다 양성을 몇 번 넣을지")
    ap.add_argument("--folds", default="0,1,2,3,4", help="바깥 fold")
    ap.add_argument("--no-inner", action="store_true", help="안쪽 모델은 건너뛴다 (바깥 점수만)")
    ap.add_argument("--fold-seed", type=int, default=-1, help="-1 = rerank_pairs.csv 의 fold, 그 밖 = 라벨 비율을 맞춘 무작위 나누기")
    ap.add_argument("--labels", default="unknown0", choices=("base", "unknown0", "agent"), help="학습 정답 (train_followup_pairs.article_label)")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()
    out_path = Path(args.out or DATA / f"followup_cross_{args.variant}_{args.labels}{'' if args.fold_seed < 0 else f'_s{args.fold_seed}'}.csv")

    import torch
    from sklearn.metrics import roc_auc_score
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    dev = torch.device(args.device or ("mps" if torch.backends.mps.is_available() else "cpu"))
    evs = TP.load_pairs()["events"]
    fold_of = {e["key"]: e["fold"] for e in evs} if args.fold_seed < 0 else TP.random_folds(evs, args.fold_seed)
    agent = TP.load_agent_labels() if args.labels == "agent" else None
    rows = []
    for ev in evs:
        for c in TP.candidates(ev):
            a, b = texts(ev, c, args.variant)
            rows.append({"key": ev["key"], "news_id": c["news_id"], "fold": fold_of[ev["key"]], "y": TP.article_label(ev, c, args.labels, agent), "a": a, "b": b})
    print(f"후보 {len(rows)} · 정답 1 {sum(r['y'] == 1 for r in rows)} · 정답 0 {sum(r['y'] == 0 for r in rows)} · 모름 {sum(r['y'] is None for r in rows)} · 장치 {dev}", flush=True)
    tok = AutoTokenizer.from_pretrained(MODEL)

    def batches(items, bs):
        for i in range(0, len(items), bs):
            b = items[i : i + bs]
            enc = tok([x["a"] for x in b], [x["b"] for x in b], truncation="only_second", max_length=args.max_len, padding=True, return_tensors="pt")
            enc.pop("token_type_ids", None)  # RoBERTa: type_vocab_size=1
            yield b, {k: v.to(dev) for k, v in enc.items()}

    def fit(train, name, seed, t0):
        random.seed(seed)
        torch.manual_seed(seed)
        pos = [r for r in train if r["y"] == 1]
        neg = [r for r in train if r["y"] == 0]
        model = AutoModelForSequenceClassification.from_pretrained(MODEL, num_labels=1).to(dev)
        opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
        per_epoch = len(pos) * args.pos_repeat + min(len(neg), args.neg_per_epoch)
        steps = args.epochs * math.ceil(per_epoch / args.batch)
        sched = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
        model.train()
        for ep in range(args.epochs):
            items = pos * args.pos_repeat + random.sample(neg, min(len(neg), args.neg_per_epoch))
            random.shuffle(items)
            loss_sum, n = 0.0, 0
            for b, enc in batches(items, args.batch):
                y = torch.tensor([float(x["y"]) for x in b], device=dev)
                logit = model(**enc).logits.squeeze(-1)
                loss = torch.nn.functional.binary_cross_entropy_with_logits(logit, y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                sched.step()
                opt.zero_grad()
                loss_sum += float(loss.detach())
                n += 1
            print(f"  {name} epoch {ep}: loss {loss_sum / max(1, n):.4f} (양성 {len(pos)}×{args.pos_repeat} · 음성 {min(len(neg), args.neg_per_epoch)}/{len(neg)}) [{time.time() - t0:.0f}s]",
                  flush=True)
        return model

    def predict(model, items):
        model.eval()
        probs = []
        with torch.no_grad():
            for _b, enc in batches(items, 128):
                probs += torch.sigmoid(model(**enc).logits.squeeze(-1)).float().cpu().tolist()
        return probs

    out_rows: list[dict] = []
    done: set[tuple[int, str]] = set()
    if out_path.exists():  # 이어 하기 (바깥 fold × 역할 단위)
        with out_path.open(encoding="utf-8", newline="") as f:
            out_rows = list(csv.DictReader(f))
        done = {(int(r["outer"]), r["role"]) for r in out_rows}

    def save():
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["event_key", "news_id", "outer", "role", "inner", "y", "p"])
            w.writeheader()
            w.writerows(out_rows)

    t_all = time.time()
    folds = sorted({r["fold"] for r in rows})
    for k in [int(x) for x in args.folds.split(",") if x.strip()]:
        jobs = [("outer", None)] + ([] if args.no_inner else [("inner", j) for j in folds if j != k])
        for role, j in jobs:
            tag = role if j is None else f"inner{j}"
            if (k, tag) in done:
                print(f"fold {k} {tag}: 이미 있음", flush=True)
                continue
            t0 = time.time()
            if role == "outer":
                train = [r for r in rows if r["fold"] != k and r["y"] is not None]
                test = [r for r in rows if r["fold"] == k]
            else:
                train = [r for r in rows if r["fold"] not in (k, j) and r["y"] is not None]
                test = [r for r in rows if r["fold"] == j]
            model = fit(train, f"fold {k} {tag}", 1000 * k + (j if j is not None else 99), t0)
            probs = predict(model, test)
            lab = [(r["y"], p) for r, p in zip(test, probs, strict=True) if r["y"] is not None]
            auc = roc_auc_score([y for y, _ in lab], [p for _, p in lab]) if len({y for y, _ in lab}) == 2 else float("nan")
            print(f"fold {k} {tag}: 학습 {len(train)} · 평가 {len(test)} · 기사 AUC {auc:.4f} · {time.time() - t0:.0f}s", flush=True)
            out_rows += [{"event_key": r["key"], "news_id": r["news_id"], "outer": k, "role": tag, "inner": "" if j is None else j, "y": "" if r["y"] is None else r["y"],
                          "p": f"{p:.5f}"} for r, p in zip(test, probs, strict=True)]
            save()
            del model
            if dev.type == "mps":
                torch.mps.empty_cache()
    print(f"끝 · {len(out_rows)}행 · {time.time() - t_all:.0f}s → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
