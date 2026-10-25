"""접근법 C — 교차 인코더 미세조정, 사건 단위 5-fold 교차검증 (docs/EXPERIMENT.md §9).

  cd backend
  .venv/bin/python scripts/train_cross_encoder.py                 # data/experiment/rerank_pairs.csv 의 fold 로 학습·예측 → rerank_cross_oof.csv
  .venv/bin/python scripts/train_cross_encoder.py --sample 0.5    # 학습 행 표본 (시간이 길 때)
  .venv/bin/python scripts/train_cross_encoder.py --folds "" --save   # 라벨 전체로 학습 → models/cross_encoder/ (LINK_RERANKER=cross, gitignore)
                                                                      # 교차검증 OOF 확률도 oof_scores.csv 로 복사 (RERANK_CROSSFIT: 학습에 쓴 사건은 OOF 확률)

입력 쌍 = (사건 텍스트: 사건명·지역·시설·핵심어, 기사: [발생 후 기간] 제목 하이라이트 200자). 모델 = 이미 받아 둔 jhgan/ko-sroberta-multitask 에
회귀 머리 1개를 붙여 BCE 로 미세조정 (2 epoch, lr 2e-5, batch 32, 최대 128 토큰). 장치: MPS → CPU.
ko-sroberta 의 토크나이저는 문장 쌍에 segment id 1 을 내지만 모델(RoBERTa)은 segment 가 하나뿐이라 token_type_ids 를 넘기지 않는다.
fold 모델은 저장하지 않는다(fold 당 440MB) — 교차검증 확률(OOF)만 남기고, --save 는 라벨 전체로 학습한 모델 하나를 fp16(220MB)으로 저장한다.
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

from app.pipeline.rerank import MODELS_DIR, date_bucket  # noqa: E402
DATA = BACKEND / "data" / "experiment"
MODEL = "jhgan/ko-sroberta-multitask"


def bucket(days: str) -> str:
    """pipeline/rerank.date_bucket 과 같은 표지 (CSV 의 days 는 문자열, 날짜 없으면 빈 값)."""
    try:
        return date_bucket(int(days))
    except (TypeError, ValueError):
        return date_bucket(None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=str(DATA / "rerank_pairs.csv"))
    ap.add_argument("--out", default=str(DATA / "rerank_cross_oof.csv"))
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--sample", type=float, default=1.0, help="학습 행 표본 비율 (0~1)")
    ap.add_argument("--folds", default="0,1,2,3,4")
    ap.add_argument("--device", default=None)
    ap.add_argument("--save", action="store_true", help="라벨 전체로 한 번 더 학습해 models/cross_encoder/ 에 저장 (+ OOF 확률 복사)")
    ap.add_argument("--save-dir", default=str(MODELS_DIR / "cross_encoder"))
    args = ap.parse_args()

    import torch
    from sklearn.metrics import roc_auc_score
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    dev = torch.device(args.device or ("mps" if torch.backends.mps.is_available() else "cpu"))
    with open(args.pairs, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["in_domain"] == "1"]
    for r in rows:
        r["fold"] = int(r["fold"])
        r["a"] = r["event_text"]
        r["b"] = f"{bucket(r['days'])} {r['title']} {r['hilight']}".strip()  # = rerank.cross_texts
    tok = AutoTokenizer.from_pretrained(MODEL)

    def batches(items, bs):
        for i in range(0, len(items), bs):
            b = items[i : i + bs]
            enc = tok([x["a"] for x in b], [x["b"] for x in b], truncation="only_second", max_length=args.max_len, padding=True, return_tensors="pt")
            enc.pop("token_type_ids", None)  # RoBERTa: type_vocab_size=1
            yield b, {k: v.to(dev) for k, v in enc.items()}

    def fit(train, name, t0):
        model = AutoModelForSequenceClassification.from_pretrained(MODEL, num_labels=1).to(dev)
        opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
        steps = args.epochs * math.ceil(len(train) / args.batch)
        sched = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
        model.train()
        for ep in range(args.epochs):
            random.shuffle(train)
            loss_sum = 0.0
            for b, enc in batches(train, args.batch):
                y = torch.tensor([1.0 if x["label"] == "O" else 0.0 for x in b], device=dev)
                logit = model(**enc).logits.squeeze(-1)
                loss = torch.nn.functional.binary_cross_entropy_with_logits(logit, y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                sched.step()
                opt.zero_grad()
                loss_sum += float(loss.detach())
            print(f"{name} epoch {ep}: loss {loss_sum / max(1, math.ceil(len(train) / args.batch)):.4f} [{time.time() - t0:.0f}s]", flush=True)
        return model

    out_rows = []
    done = set()
    out_path = Path(args.out)
    if out_path.exists():  # 이어 하기 (fold 단위)
        with out_path.open(encoding="utf-8", newline="") as f:
            prev = list(csv.DictReader(f))
        done = {int(r["fold"]) for r in prev}
        out_rows = prev
    t_all = time.time()
    for k in [int(x) for x in args.folds.split(",") if x.strip()]:
        if k in done:
            print(f"fold {k}: 이미 있음 — 건너뜀", flush=True)
            continue
        t0 = time.time()
        random.seed(k)
        torch.manual_seed(k)
        train = [r for r in rows if r["fold"] != k and r["label"] in ("O", "X")]
        if args.sample < 1.0:
            train = random.sample(train, int(len(train) * args.sample))
        test = [r for r in rows if r["fold"] == k]
        model = fit(train, f"fold {k}", t0)
        model.eval()
        probs = []
        with torch.no_grad():
            for b, enc in batches(test, 128):
                probs += torch.sigmoid(model(**enc).logits.squeeze(-1)).float().cpu().tolist()
        lab = [(r["label"] == "O", p) for r, p in zip(test, probs) if r["label"] in ("O", "X")]
        a = roc_auc_score([x for x, _ in lab], [p for _, p in lab]) if lab else float("nan")
        print(f"fold {k}: 학습 {len(train)} · 평가 {len(test)} (라벨 {len(lab)}) · AUC {a:.4f} · {time.time() - t0:.0f}s", flush=True)
        out_rows += [{"event_key": r["event_key"], "news_id": r["news_id"], "fold": k, "label": r["label"], "p": f"{p:.4f}"} for r, p in zip(test, probs)]
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["event_key", "news_id", "fold", "label", "p"])
            w.writeheader()
            w.writerows(out_rows)
        del model
        if dev.type == "mps":
            torch.mps.empty_cache()
    lab = [(r["label"] == "O", float(r["p"])) for r in out_rows if r["label"] in ("O", "X")]
    if lab:
        print(f"전체 OOF AUC {roc_auc_score([x for x, _ in lab], [p for _, p in lab]):.4f} · {len(out_rows)}행 · {time.time() - t_all:.0f}s → {out_path}")
    if args.save:
        import json
        import shutil
        from datetime import datetime

        t0 = time.time()
        random.seed(0)
        torch.manual_seed(0)
        train = [r for r in rows if r["label"] in ("O", "X")]
        model = fit(train, "full", t0)
        d = Path(args.save_dir)
        d.mkdir(parents=True, exist_ok=True)
        model.half().save_pretrained(d)  # fp16 로 저장 (220MB) — 읽을 때 fp32 로 되돌린다 (rerank.CrossReranker)
        tok.save_pretrained(d)
        pmeta = Path(args.pairs).with_suffix(".meta.json")
        emb = json.loads(pmeta.read_text(encoding="utf-8")).get("embedding") if pmeta.exists() else None
        meta = {"base_model": MODEL, "trained_at": datetime.now().isoformat(timespec="seconds"), "rows": len(train), "epochs": args.epochs, "lr": args.lr,
                "embedding": emb,  # 재순위 대상(link·review 판정)을 만든 임베딩 — 다른 임베딩에서는 쓰지 않는다
                "batch": args.batch, "max_len": args.max_len, "labels": "gold/cluster_full.csv (Claude 에이전트 라벨)", "oof": out_path.name}
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        if out_path.exists():
            shutil.copy(out_path, d / "oof_scores.csv")
        print(f"전체 모델 → {d} ({time.time() - t0:.0f}s) · {meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
